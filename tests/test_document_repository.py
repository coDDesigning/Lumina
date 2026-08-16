from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.models import (
    DocumentChunk,
    DocumentPage,
    DocumentVisual,
    UploadedDocument,
)
from backend.app.repositories.document import DocumentRepository

pytestmark = pytest.mark.database_contract


def document_values(
    model_graph,
    *,
    document_id: UUID | None = None,
    course_id: int | None = None,
    file_hash: str = "a" * 64,
    **overrides: object,
) -> dict[str, object]:
    values: dict[str, object] = {
        "id": document_id or uuid4(),
        "original_file_name": "notes.txt",
        "file_type": "txt",
        "mime_type": "text/plain",
        "file_size": 12,
        "file_hash": file_hash,
        "user_id": model_graph.user.id,
        "course_id": course_id or model_graph.course.id,
        "storage_provider": "local",
        "storage_key": f"test/{uuid4()}/source.txt",
        "status": "uploaded",
    }
    values.update(overrides)
    return values


def test_uuid_primary_key_roundtrips_through_database(
    db_session: Session,
    model_graph,
) -> None:
    document_id = UUID("12345678-1234-5678-9234-567812345678")
    DocumentRepository.create(
        db_session,
        **document_values(model_graph, document_id=document_id),
    )
    db_session.commit()
    db_session.expunge_all()

    loaded = DocumentRepository.get_by_id(db_session, document_id)

    assert loaded is not None
    assert loaded.id == document_id
    assert isinstance(loaded.id, UUID)


def test_same_course_and_hash_is_unique(
    db_session: Session,
    model_graph,
) -> None:
    file_hash = "b" * 64
    DocumentRepository.create(
        db_session,
        **document_values(model_graph, file_hash=file_hash),
    )
    db_session.commit()

    with pytest.raises(IntegrityError):
        DocumentRepository.create(
            db_session,
            **document_values(model_graph, file_hash=file_hash),
        )
    db_session.rollback()

    assert db_session.scalar(select(func.count()).select_from(UploadedDocument)) == 1


def test_same_hash_is_allowed_in_different_courses(
    db_session: Session,
    model_graph,
) -> None:
    file_hash = "c" * 64
    first = DocumentRepository.create(
        db_session,
        **document_values(
            model_graph,
            course_id=model_graph.course.id,
            file_hash=file_hash,
        ),
    )
    second = DocumentRepository.create(
        db_session,
        **document_values(
            model_graph,
            course_id=model_graph.other_course.id,
            file_hash=file_hash,
        ),
    )
    db_session.commit()

    assert first.id != second.id
    assert db_session.scalar(select(func.count()).select_from(UploadedDocument)) == 2


def test_storage_location_is_unique(
    db_session: Session,
    model_graph,
) -> None:
    storage_key = "contract/shared/source.txt"
    DocumentRepository.create(
        db_session,
        **document_values(
            model_graph,
            storage_provider="local:contract",
            storage_key=storage_key,
        ),
    )
    db_session.commit()

    with pytest.raises(IntegrityError):
        DocumentRepository.create(
            db_session,
            **document_values(
                model_graph,
                file_hash="9" * 64,
                storage_provider="local:contract",
                storage_key=storage_key,
            ),
        )
    db_session.rollback()


def test_get_by_course_and_hash_is_course_scoped(
    db_session: Session,
    model_graph,
) -> None:
    file_hash = "d" * 64
    created = DocumentRepository.create(
        db_session,
        **document_values(model_graph, file_hash=file_hash),
    )
    db_session.commit()

    found = DocumentRepository.get_by_course_and_hash(
        db_session,
        model_graph.course.id,
        file_hash,
    )
    not_found = DocumentRepository.get_by_course_and_hash(
        db_session,
        model_graph.other_course.id,
        file_hash,
    )

    assert found is not None
    assert found.id == created.id
    assert not_found is None


def test_update_status_and_processing_error(
    db_session: Session,
    model_graph,
) -> None:
    document = DocumentRepository.create(
        db_session,
        **document_values(model_graph),
    )
    db_session.commit()

    updated = DocumentRepository.update_status(
        db_session,
        document.id,
        "failed",
        "OCR\x00 failed",
    )
    db_session.commit()

    assert updated is not None
    assert updated.status == "failed"
    assert updated.processing_error == "OCR failed"
    assert DocumentRepository.update_status(db_session, uuid4(), "ready") is None
    with pytest.raises(ValueError, match="Unsupported document status"):
        DocumentRepository.update_status(db_session, document.id, "queued")


def test_delete_reports_whether_a_document_existed(
    db_session: Session,
    model_graph,
) -> None:
    document = DocumentRepository.create(
        db_session,
        **document_values(model_graph),
    )
    db_session.commit()
    document_id = document.id

    assert DocumentRepository.delete(db_session, document_id) is True
    db_session.commit()
    assert DocumentRepository.get_by_id(db_session, document_id) is None
    assert DocumentRepository.delete(db_session, document_id) is False


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("file_hash", "e" * 63),
        ("file_size", -1),
        ("status", "queued"),
        ("user_id", 999_999),
        ("course_id", 999_999),
    ],
    ids=["hash-length", "negative-size", "status", "user-fk", "course-fk"],
)
def test_document_database_constraints_are_enforced(
    db_session: Session,
    model_graph,
    field: str,
    invalid_value: object,
) -> None:
    values = document_values(model_graph)
    values[field] = invalid_value

    with pytest.raises(IntegrityError):
        DocumentRepository.create(db_session, **values)
    db_session.rollback()

    assert db_session.scalar(select(func.count()).select_from(UploadedDocument)) == 0


def test_document_chunk_course_must_match_its_document(
    db_session: Session,
    model_graph,
) -> None:
    document = DocumentRepository.create(db_session, **document_values(model_graph))
    db_session.commit()

    db_session.add(
        DocumentChunk(
            document_id=document.id,
            course_id=model_graph.other_course.id,
            chunk_index=0,
            text="wrong course",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    assert db_session.scalar(select(func.count()).select_from(DocumentChunk)) == 0


@pytest.mark.parametrize(
    "overrides",
    [{"chunk_index": -1}, {"page_number": 0}],
    ids=["chunk-index", "page-number"],
)
def test_document_chunk_constraints_are_enforced(
    db_session: Session,
    model_graph,
    overrides: dict[str, object],
) -> None:
    document = DocumentRepository.create(db_session, **document_values(model_graph))
    values: dict[str, object] = {
        "document": document,
        "course": model_graph.course,
        "chunk_index": 0,
        "page_number": 1,
        "text": "chunk",
    }
    values.update(overrides)
    db_session.add(DocumentChunk(**values))

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


@pytest.mark.parametrize(
    "overrides",
    [
        {"page_number": None, "has_images": False, "needs_ocr": True},
        {"raw_extraction_method": "ocr"},
        {"extraction_method": "unknown"},
        {"ocr_status": "unknown"},
        {"visual_analysis_status": "unknown"},
        {"content_index": -1},
        {"page_number": 0},
        {
            "raw_needs_ocr": True,
            "page_number": None,
            "has_images": False,
            "has_visual_content": False,
        },
    ],
    ids=[
        "ocr-candidate",
        "raw-extraction-method",
        "extraction-method",
        "ocr-status",
        "visual-status",
        "content-index",
        "page-number",
        "raw-ocr-candidate",
    ],
)
def test_document_page_constraints_are_enforced(
    db_session: Session,
    model_graph,
    overrides: dict[str, object],
) -> None:
    document = DocumentRepository.create(db_session, **document_values(model_graph))
    db_session.commit()

    values: dict[str, object] = {
        "document_id": document.id,
        "course_id": model_graph.course.id,
        "content_index": 0,
        "page_number": 1,
        "raw_text": "Raw page",
        "text": "Effective page",
        "raw_extraction_method": "native",
        "extraction_method": "ocr",
        "has_images": True,
        "needs_ocr": False,
        "ocr_status": "succeeded",
        "has_visual_content": False,
        "visual_analysis_status": "not_applicable",
    }
    values.update(overrides)
    db_session.add(DocumentPage(**values))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    assert db_session.scalar(select(func.count()).select_from(DocumentPage)) == 0


def test_document_page_course_must_match_its_document(
    db_session: Session,
    model_graph,
) -> None:
    document = DocumentRepository.create(db_session, **document_values(model_graph))
    db_session.add(
        DocumentPage(
            document_id=document.id,
            course_id=model_graph.other_course.id,
            content_index=0,
            page_number=1,
            raw_text="Raw page",
            text="Page",
            raw_extraction_method="native",
            extraction_method="native",
            has_images=False,
            needs_ocr=False,
        )
    )

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


@pytest.mark.parametrize(
    "overrides",
    [
        {"visual_index": -1},
        {"visual_type": "photograph"},
        {"source": "unknown"},
        {"bbox_x1": 0.0},
        {"analysis_status": "unknown"},
        {"description": "Not analyzed"},
        {"analysis_status": "failed"},
        {"analysis_status": "failed", "error_code": "   "},
        {"analysis_status": "failed", "error_code": "\t\n"},
    ],
    ids=[
        "visual-index",
        "visual-type",
        "source",
        "bbox",
        "analysis-status",
        "description-status",
        "failed-error-code",
        "blank-failed-error-code",
        "whitespace-failed-error-code",
    ],
)
def test_document_visual_constraints_are_enforced(
    db_session: Session,
    model_graph,
    overrides: dict[str, object],
) -> None:
    document = DocumentRepository.create(db_session, **document_values(model_graph))
    page = DocumentPage(
        document=document,
        course=model_graph.course,
        content_index=0,
        page_number=1,
        raw_text="Raw page",
        text="Effective page",
        raw_extraction_method="native",
        extraction_method="native",
        has_images=True,
        needs_ocr=False,
        has_visual_content=True,
        visual_analysis_status="pending",
    )
    values: dict[str, object] = {
        "page": page,
        "visual_index": 0,
        "visual_type": "figure",
        "source": "image",
        "bbox_x0": 0.0,
        "bbox_y0": 0.0,
        "bbox_x1": 10.0,
        "bbox_y1": 10.0,
        "analysis_status": "pending",
    }
    values.update(overrides)
    db_session.add(DocumentVisual(**values))

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    assert db_session.scalar(select(func.count()).select_from(DocumentVisual)) == 0
