"""Deterministic end-to-end visual analysis and semantic retrieval tests.

Proves the complete chain required by SCRUM-159:
diagram-bearing PDF -> visual detected -> visual description generated ->
description persisted in DocumentVisual -> description merged into page & chunk text ->
chunk embedding & vector indexing -> semantic retrieval returns visual-derived chunk.
"""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import pymupdf
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from backend.app.models import (
    EMBEDDING_DIMENSIONS,
    Course,
    DocumentChunk,
    DocumentPage,
    DocumentVisual,
    ProcessingJob,
    Role,
    UploadedDocument,
    User,
)
from services.document_embedding import EMBEDDING_STAGE, embed_document_chunks
from services.document_extraction import extract_document
from services.document_pipeline import (
    PageVisualAnalysisStatus,
    VisualAnalysisError,
    VisualAnalysisStatus,
    VisualDescription,
    VisualType,
)
from services.processing_jobs import (
    claim_next_job,
    complete_job,
    enqueue_document_job,
    replace_document_pages,
    update_job_stage,
)
from services.semantic_retrieval import retrieve_course_chunks
from services.vector_store import (
    ChromaVectorStore,
    PgVectorStore,
    VectorRecord,
    VectorStore,
)
from storage.local import LocalStorage

pytestmark = pytest.mark.database_contract

CANARY_TOPOLOGY_DESCRIPTION = (
    "CANARY_CIRCUIT_BREAKER_MESH_TOPOLOGY_FAILOVER_ISOLATION_DIAGRAM"
)
CANARY_SECONDARY_DESCRIPTION = (
    "CANARY_LOAD_BALANCER_ROUND_ROBIN_TRAFFIC_DISTRIBUTION_CHART"
)


def _directional(seed: float) -> list[float]:
    values = [0.0] * EMBEDDING_DIMENSIONS
    values[0] = 1.0
    values[1] = seed
    return values


class StubCanaryEmbeddingProvider:
    """Deterministic embedding provider that maps visual canary text to high-similarity vector."""

    def __init__(self) -> None:
        self.embed_query_calls: list[str] = []
        self.embed_doc_calls: list[list[str]] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.embed_doc_calls.append(list(texts))
        results: list[list[float]] = []
        for text in texts:
            if CANARY_TOPOLOGY_DESCRIPTION in text:
                results.append(_directional(0.01))
            elif CANARY_SECONDARY_DESCRIPTION in text:
                results.append(_directional(0.05))
            else:
                results.append(_directional(5.0))
        return results

    def embed_query(self, text: str) -> list[float]:
        self.embed_query_calls.append(text)
        return _directional(0.0)


class MockCanaryVisionProvider:
    """Fake vision provider returning deterministic canary descriptions."""

    enabled = True

    def __init__(
        self,
        descriptions: dict[int, str] | None = None,
        failing_pages: set[int] | None = None,
    ) -> None:
        self.descriptions = descriptions or {1: CANARY_TOPOLOGY_DESCRIPTION}
        self.failing_pages = failing_pages or set()
        self.calls: list[tuple[int, int, VisualType]] = []

    def describe_visual(
        self,
        visual_png: bytes,
        *,
        page_number: int,
        visual_index: int,
        suggested_type: VisualType,
    ) -> VisualDescription:
        self.calls.append((page_number, visual_index, suggested_type))
        if page_number in self.failing_pages:
            raise VisualAnalysisError(f"Corrupt visual region on page {page_number}")

        description = self.descriptions.get(
            page_number,
            f"Visual on page {page_number} index {visual_index}",
        )
        return VisualDescription(
            visual_type=suggested_type,
            description=description,
        )


def _generate_diagram_pdf(
    page_specs: list[str] | None = None,
) -> bytes:
    """Generate a genuine multi-page PDF with vector graphics diagrams and image regions."""
    pages = page_specs or [
        "Distributed systems core concepts: service discovery and gateway routing."
    ]
    doc = pymupdf.open()
    for page_idx, text_content in enumerate(pages, start=1):
        page = doc.new_page(width=595, height=842)
        page.insert_text((50, 50), text_content)

        # Draw vector graphics diagram (boxes and connectors)
        page.draw_rect(
            pymupdf.Rect(100, 100, 220, 180),
            color=(0, 0, 1),
            fill=(0.9, 0.9, 1),
            width=1.5,
        )
        page.draw_rect(
            pymupdf.Rect(300, 100, 420, 180),
            color=(1, 0, 0),
            fill=(1, 0.9, 0.9),
            width=1.5,
        )
        page.draw_line(
            pymupdf.Point(220, 140),
            pymupdf.Point(300, 140),
            color=(0, 0, 0),
            width=2,
        )

        # Insert raster image element
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 16, 16), False)
        page.insert_image(pymupdf.Rect(100, 220, 200, 320), pixmap=pix)

    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def _setup_course_and_document(
    session: Session,
    storage: LocalStorage,
    pdf_bytes: bytes,
    email: str = "visual-e2e@example.com",
) -> tuple[Course, UploadedDocument, ProcessingJob]:
    role = session.scalar(select(Role).where(Role.name == "user"))
    assert role is not None
    user = User(
        name="Visual Learner",
        email=email,
        password_hash="not-a-real-hash",
        role=role,
    )
    course = Course(title="Distributed Systems Visuals", owner=user)
    session.add_all((user, course))
    session.flush()

    doc_id = uuid4()
    storage_key = storage.generate_key(course.id, doc_id, "pdf")
    storage.save(storage_key, BytesIO(pdf_bytes))

    document = UploadedDocument(
        id=doc_id,
        original_file_name="distributed_systems.pdf",
        file_type="pdf",
        mime_type="application/pdf",
        file_size=len(pdf_bytes),
        file_hash=hashlib.sha256(pdf_bytes).hexdigest(),
        uploader=user,
        course=course,
        storage_provider=storage.provider,
        storage_key=storage_key,
        status="uploaded",
    )
    session.add(document)
    session.flush()
    job = enqueue_document_job(session, document)
    session.commit()
    return course, document, job


def test_end_to_end_visual_detection_description_and_retrieval(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    """Proves: diagram PDF -> visual detected -> description -> persisted -> embedded -> retrieved."""
    storage = LocalStorage(tmp_path / "e2e-uploads")
    vector_store = ChromaVectorStore(persist_directory=str(tmp_path / "chroma-e2e"))
    embedding_provider = StubCanaryEmbeddingProvider()
    vision_provider = MockCanaryVisionProvider(
        descriptions={1: CANARY_TOPOLOGY_DESCRIPTION}
    )

    pdf_content = _generate_diagram_pdf(
        ["Chapter 1: Standard textual overview of cluster orchestration."]
    )

    with session_factory() as session:
        course, document, job = _setup_course_and_document(
            session, storage, pdf_content
        )
        course_id = course.id
        doc_id = document.id

    # 1. Claim job and run extraction
    with session_factory() as session:
        claim = claim_next_job(session, "test-worker", storage.provider, 60)
        assert claim is not None
        assert claim.id == job.id

    extraction_result = extract_document(
        storage,
        storage_provider=document.storage_provider,
        storage_key=document.storage_key,
        expected_hash=document.file_hash,
        expected_size=document.file_size,
        file_type="pdf",
        image_provider=vision_provider,
    )

    # Assert visual detection & description generation
    assert len(extraction_result.pages) == 1
    extracted_page = extraction_result.pages[0]
    assert extracted_page.has_visual_content is True
    assert extracted_page.visual_analysis_status == PageVisualAnalysisStatus.COMPLETED.value
    assert len(extracted_page.visuals) >= 1
    assert extracted_page.visuals[0].analysis_status == VisualAnalysisStatus.SUCCEEDED.value
    assert extracted_page.visuals[0].description == CANARY_TOPOLOGY_DESCRIPTION
    assert CANARY_TOPOLOGY_DESCRIPTION in extracted_page.text

    # Assert canary made it into at least one chunk
    canary_chunks = [
        c for c in extraction_result.chunks if CANARY_TOPOLOGY_DESCRIPTION in c.text
    ]
    assert len(canary_chunks) >= 1

    # 2. Stage update, raw page replacement, embeddings, and job completion
    with session_factory() as session:
        assert update_job_stage(session, claim.id, claim.claim_token, "extracting_text")
        assert replace_document_pages(
            session, claim.id, claim.claim_token, extraction_result.pages
        )
        assert update_job_stage(session, claim.id, claim.claim_token, "cleaning_text")
        assert update_job_stage(session, claim.id, claim.claim_token, "chunking")
        assert update_job_stage(session, claim.id, claim.claim_token, EMBEDDING_STAGE)

        embeddings = embed_document_chunks(
            [chunk.text for chunk in extraction_result.chunks],
            provider=embedding_provider,
        )

        completed = complete_job(
            session,
            claim.id,
            claim.claim_token,
            extraction_result.chunks,
            pages=None,
            embeddings=embeddings,
            vector_store=vector_store,
        )
        assert completed is True

    # 3. Assert database and ORM persistence at all layers
    with session_factory() as session:
        doc = session.scalar(
            select(UploadedDocument)
            .options(selectinload(UploadedDocument.pages))
            .where(UploadedDocument.id == doc_id)
        )
        assert doc is not None
        assert doc.status == "ready"
        assert doc.visual_analysis_status == "completed"

        pages = list(session.scalars(select(DocumentPage).where(DocumentPage.document_id == doc_id)))
        assert len(pages) == 1
        page = pages[0]
        assert page.has_visual_content is True
        assert page.visual_analysis_status == "completed"
        assert CANARY_TOPOLOGY_DESCRIPTION in page.text

        visuals = list(session.scalars(select(DocumentVisual).where(DocumentVisual.page_id == page.id)))
        assert len(visuals) >= 1
        assert visuals[0].analysis_status == "succeeded"
        assert visuals[0].description == CANARY_TOPOLOGY_DESCRIPTION

        chunks = list(
            session.scalars(
                select(DocumentChunk)
                .where(DocumentChunk.document_id == doc_id)
                .order_by(DocumentChunk.chunk_index)
            )
        )
        assert any(CANARY_TOPOLOGY_DESCRIPTION in c.text for c in chunks)

        # 4. Execute real semantic retrieval path
        retrieved_chunks = retrieve_course_chunks(
            session,
            course_id=course_id,
            query="circuit breaker mesh topology architecture",
            limit=3,
            provider=embedding_provider,
            store=vector_store,
        )

        assert len(retrieved_chunks) >= 1
        # Top-ranked chunk must be the visual-derived chunk containing the canary phrase
        top_chunk = retrieved_chunks[0]
        assert CANARY_TOPOLOGY_DESCRIPTION in top_chunk.text
        assert top_chunk.similarity > 0.95
        assert top_chunk.document_id == doc_id
        assert top_chunk.page_number == 1


def test_partial_visual_analysis_success_isolates_failures(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    """Proves: multi-page PDF with 1 visual success and 1 visual failure rolls up to partial."""
    storage = LocalStorage(tmp_path / "partial-uploads")
    vector_store = ChromaVectorStore(persist_directory=str(tmp_path / "chroma-partial"))
    embedding_provider = StubCanaryEmbeddingProvider()

    # Page 1 succeeds, Page 2 fails visual analysis
    vision_provider = MockCanaryVisionProvider(
        descriptions={1: CANARY_TOPOLOGY_DESCRIPTION},
        failing_pages={2},
    )

    pdf_content = _generate_diagram_pdf(
        [
            "Page 1 textual discussion on service failover topologies.",
            "Page 2 textual discussion on hardware node configurations.",
        ]
    )

    with session_factory() as session:
        course, document, job = _setup_course_and_document(
            session, storage, pdf_content, email="partial-vis@example.com"
        )
        course_id = course.id
        doc_id = document.id

    with session_factory() as session:
        claim = claim_next_job(session, "partial-worker", storage.provider, 60)
        assert claim is not None

    extraction_result = extract_document(
        storage,
        storage_provider=document.storage_provider,
        storage_key=document.storage_key,
        expected_hash=document.file_hash,
        expected_size=document.file_size,
        file_type="pdf",
        image_provider=vision_provider,
    )

    assert len(extraction_result.pages) == 2
    p1 = extraction_result.pages[0]
    p2 = extraction_result.pages[1]

    assert p1.visual_analysis_status == PageVisualAnalysisStatus.COMPLETED.value
    assert p1.visuals[0].analysis_status == VisualAnalysisStatus.SUCCEEDED.value
    assert p1.visuals[0].description == CANARY_TOPOLOGY_DESCRIPTION

    assert p2.visual_analysis_status == PageVisualAnalysisStatus.FAILED.value
    assert p2.visuals[0].analysis_status == VisualAnalysisStatus.FAILED.value
    assert p2.visuals[0].error_code == "VISUAL_ANALYSIS_FAILED"
    assert p2.visuals[0].description is None

    # Complete the job
    with session_factory() as session:
        assert update_job_stage(session, claim.id, claim.claim_token, "extracting_text")
        assert replace_document_pages(
            session, claim.id, claim.claim_token, extraction_result.pages
        )
        assert update_job_stage(session, claim.id, claim.claim_token, "cleaning_text")
        assert update_job_stage(session, claim.id, claim.claim_token, "chunking")
        assert update_job_stage(session, claim.id, claim.claim_token, EMBEDDING_STAGE)

        embeddings = embed_document_chunks(
            [chunk.text for chunk in extraction_result.chunks],
            provider=embedding_provider,
        )

        completed = complete_job(
            session,
            claim.id,
            claim.claim_token,
            extraction_result.chunks,
            pages=None,
            embeddings=embeddings,
            vector_store=vector_store,
        )
        assert completed is True

    # Assert document rollup is partial
    with session_factory() as session:
        doc = session.scalar(
            select(UploadedDocument)
            .options(selectinload(UploadedDocument.pages))
            .where(UploadedDocument.id == doc_id)
        )
        assert doc is not None
        assert doc.status == "ready"
        assert doc.visual_analysis_status == "partial"

        retrieved_chunks = retrieve_course_chunks(
            session,
            course_id=course_id,
            query="failover topology",
            limit=3,
            provider=embedding_provider,
            store=vector_store,
        )
        assert len(retrieved_chunks) >= 1
        assert CANARY_TOPOLOGY_DESCRIPTION in retrieved_chunks[0].text
