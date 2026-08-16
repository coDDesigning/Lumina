"""Bounded storage reads and the deterministic document processing pipeline."""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass

from backend.app.config import settings
from services.document_pipeline import (
    DocumentProcessingError as PipelineProcessingError,
)
from services.document_pipeline import (
    ExtractedDocument,
    EnrichedPage,
    PipelineOptions,
    PipelineStage,
    process_document,
)
from services.processing_jobs import ChunkData, PageData, VisualData
from storage.base import Storage, StorageError

StageCallback = Callable[[PipelineStage], None]
ExtractionCallback = Callable[[list[PageData]], None]


@dataclass(frozen=True, slots=True)
class ProcessedDocumentData:
    pages: list[PageData]
    chunks: list[ChunkData]


class DocumentProcessingError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        failed_stage: str | None = None,
    ) -> None:
        self.code = code
        self.retryable = retryable
        self.failed_stage = failed_stage
        super().__init__(message)


def extract_document(
    storage: Storage,
    *,
    storage_provider: str,
    storage_key: str,
    expected_hash: str,
    expected_size: int,
    file_type: str,
    stage_callback: StageCallback | None = None,
    extraction_callback: ExtractionCallback | None = None,
) -> ProcessedDocumentData:
    if storage.provider != storage_provider:
        raise DocumentProcessingError(
            "STORAGE_PROVIDER_UNAVAILABLE",
            "The document storage provider is not available to this worker.",
            retryable=True,
        )

    try:
        with storage.open(storage_key) as stored_file:
            content = stored_file.read(settings.max_upload_size_bytes + 1)
    except StorageError as exc:
        raise DocumentProcessingError(
            "STORAGE_READ_FAILED",
            "The uploaded document could not be read from storage.",
            retryable=True,
        ) from exc
    except (OSError, TypeError) as exc:
        raise DocumentProcessingError(
            "STORAGE_READ_FAILED",
            "The uploaded document could not be read from storage.",
            retryable=True,
        ) from exc
    except ValueError as exc:
        raise DocumentProcessingError(
            "STORAGE_KEY_INVALID",
            "The uploaded document has an invalid storage key.",
            retryable=False,
        ) from exc

    if not isinstance(content, bytes):
        raise DocumentProcessingError(
            "STORAGE_READ_FAILED",
            "The uploaded document could not be read from storage.",
            retryable=True,
        )
    if len(content) > settings.max_upload_size_bytes:
        raise DocumentProcessingError(
            "DOCUMENT_SIZE_LIMIT_EXCEEDED",
            "The stored document exceeds the processing size limit.",
            retryable=False,
        )
    if len(content) != expected_size:
        raise DocumentProcessingError(
            "STORAGE_SIZE_MISMATCH",
            "The stored document size no longer matches its recorded size.",
            retryable=False,
        )
    if hashlib.sha256(content).hexdigest() != expected_hash:
        raise DocumentProcessingError(
            "STORAGE_HASH_MISMATCH",
            "The stored document no longer matches its recorded hash.",
            retryable=False,
        )

    extracted_pages: list[PageData] = []

    def report_extraction(document: ExtractedDocument) -> None:
        nonlocal extracted_pages
        extracted_pages = [
            PageData(
                content_index=content.content_index,
                text=content.text,
                page_number=content.page_number,
                extraction_method=(
                    content.extraction_method.value
                    if content.extraction_method is not None
                    else None
                ),
                has_images=content.has_images,
                needs_ocr=content.needs_ocr,
                raw_text=content.text,
                raw_extraction_method=(
                    content.extraction_method.value
                    if content.extraction_method is not None
                    else None
                ),
                has_visual_content=content.has_visual_content,
                raw_needs_ocr=content.needs_ocr,
                ocr_status="pending" if content.needs_ocr else "not_required",
                visual_analysis_status=(
                    "pending" if content.has_visual_content else "not_applicable"
                ),
                visuals=tuple(
                    VisualData(
                        visual_index=visual.visual_index,
                        visual_type=visual.visual_type.value,
                        source=visual.source.value,
                        bbox=visual.bbox,
                    )
                    for visual in content.visuals
                ),
            )
            for content in document.contents
        ]
        if extraction_callback is not None:
            extraction_callback(extracted_pages)

    try:
        result = process_document(
            file_type,
            content,
            options=PipelineOptions(
                max_extracted_characters=settings.max_extracted_characters,
                max_document_chunks=settings.max_document_chunks,
            ),
            stage_callback=stage_callback,
            extraction_callback=report_extraction,
        )
    except PipelineProcessingError as exc:
        raise DocumentProcessingError(
            exc.code.value.upper(),
            exc.safe_message,
            retryable=exc.retryable,
            failed_stage=exc.failed_stage.value,
        ) from exc

    return ProcessedDocumentData(
        pages=[_page_data(page) for page in result.pages],
        chunks=[
            ChunkData(
                text=chunk.text,
                page_number=chunk.page_number,
                end_page_number=chunk.end_page_number,
            )
            for chunk in result.chunks
        ],
    )


def _page_data(page: EnrichedPage) -> PageData:
    return PageData(
        content_index=page.content_index,
        text=page.text,
        page_number=page.page_number,
        extraction_method=(
            page.extraction_method.value if page.extraction_method is not None else None
        ),
        has_images=page.has_images,
        needs_ocr=page.needs_ocr,
        raw_text=page.raw_text,
        raw_extraction_method=(
            page.raw_extraction_method.value
            if page.raw_extraction_method is not None
            else None
        ),
        has_visual_content=page.has_visual_content,
        raw_needs_ocr=page.raw_needs_ocr,
        ocr_status=page.ocr_status.value,
        visual_analysis_status=page.visual_analysis_status.value,
        visuals=tuple(
            VisualData(
                visual_index=visual.visual_index,
                visual_type=visual.visual_type.value,
                source=visual.source.value,
                bbox=visual.bbox,
                description=visual.description,
                analysis_status=visual.analysis_status.value,
                error_code=visual.error_code,
            )
            for visual in page.visuals
        ),
    )
