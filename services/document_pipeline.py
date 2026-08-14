"""Pure document validation, extraction, enrichment, cleaning, and chunking."""

import logging
import re
import threading
import unicodedata
import zlib
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, replace
from enum import StrEnum
from math import ceil
from typing import Protocol

import pymupdf
from charset_normalizer import from_bytes

from backend.app.config import settings
from services.document_validation import FILE_TYPES

logger = logging.getLogger(__name__)


class PipelineStage(StrEnum):
    """Worker-visible document processing stages."""

    VALIDATING = "validating"
    EXTRACTING_TEXT = "extracting_text"
    RUNNING_OCR = "running_ocr"
    UNDERSTANDING_IMAGES = "understanding_images"
    CLEANING_TEXT = "cleaning_text"
    CHUNKING = "chunking"


class ProcessingErrorCode(StrEnum):
    """Stable machine-readable processing failure codes."""

    UNSUPPORTED_FILE_TYPE = "unsupported_file_type"
    DOCUMENT_TOO_LARGE = "document_too_large"
    CORRUPTED_PDF = "corrupted_pdf"
    PASSWORD_PROTECTED_PDF = "password_protected_pdf"
    DOCUMENT_TOO_COMPLEX = "document_too_complex"
    EXTRACTED_TEXT_LIMIT_EXCEEDED = "extracted_text_limit_exceeded"
    DOCUMENT_CHUNK_LIMIT_EXCEEDED = "document_chunk_limit_exceeded"
    CORRUPTED_TEXT = "corrupted_text"
    OCR_UNAVAILABLE = "ocr_unavailable"
    OCR_FAILED = "ocr_failed"
    PAGE_RENDER_FAILED = "page_render_failed"
    VISUAL_DETECTION_FAILED = "visual_detection_failed"
    IMAGE_UNDERSTANDING_FAILED = "image_understanding_failed"
    TEXT_CLEANING_FAILED = "text_cleaning_failed"
    NO_PROCESSABLE_TEXT = "no_processable_text"
    STAGE_CALLBACK_FAILED = "stage_callback_failed"
    EXTRACTION_CALLBACK_FAILED = "extraction_callback_failed"
    PROCESSING_FAILED = "processing_failed"


_ERROR_MESSAGES = {
    ProcessingErrorCode.UNSUPPORTED_FILE_TYPE: "The stored document type is unsupported.",
    ProcessingErrorCode.DOCUMENT_TOO_LARGE: (
        "The stored document exceeds the configured size limit."
    ),
    ProcessingErrorCode.CORRUPTED_PDF: "The PDF is corrupted or invalid.",
    ProcessingErrorCode.PASSWORD_PROTECTED_PDF: (
        "Password-protected PDFs are not supported."
    ),
    ProcessingErrorCode.DOCUMENT_TOO_COMPLEX: (
        "The document exceeds the configured processing limits."
    ),
    ProcessingErrorCode.EXTRACTED_TEXT_LIMIT_EXCEEDED: (
        "The document exceeds the extracted text limit."
    ),
    ProcessingErrorCode.DOCUMENT_CHUNK_LIMIT_EXCEEDED: (
        "The document exceeds the processing chunk limit."
    ),
    ProcessingErrorCode.CORRUPTED_TEXT: (
        "The text document is corrupted or contains binary data."
    ),
    ProcessingErrorCode.OCR_UNAVAILABLE: "Local OCR is unavailable.",
    ProcessingErrorCode.OCR_FAILED: "Text recognition failed.",
    ProcessingErrorCode.PAGE_RENDER_FAILED: "A document page could not be rendered.",
    ProcessingErrorCode.VISUAL_DETECTION_FAILED: (
        "Visual content could not be detected."
    ),
    ProcessingErrorCode.IMAGE_UNDERSTANDING_FAILED: ("Image understanding failed."),
    ProcessingErrorCode.TEXT_CLEANING_FAILED: (
        "The extracted document content could not be prepared for processing."
    ),
    ProcessingErrorCode.NO_PROCESSABLE_TEXT: (
        "The document contains no processable text."
    ),
    ProcessingErrorCode.STAGE_CALLBACK_FAILED: (
        "The processing stage could not be recorded."
    ),
    ProcessingErrorCode.EXTRACTION_CALLBACK_FAILED: (
        "The extracted document content could not be recorded."
    ),
    ProcessingErrorCode.PROCESSING_FAILED: "Document processing failed.",
}


class DocumentProcessingError(Exception):
    """A safe processing failure suitable for persistence by a worker."""

    def __init__(
        self,
        code: ProcessingErrorCode,
        stage: PipelineStage,
        *,
        retryable: bool,
    ) -> None:
        self.code = code
        self.safe_message = _ERROR_MESSAGES[code]
        self.stage = stage
        self.retryable = retryable
        super().__init__(self.safe_message)

    @property
    def failed_stage(self) -> PipelineStage:
        return self.stage


@dataclass(frozen=True, slots=True)
class PipelineOptions:
    """Resource, OCR, rendering, and chunking controls for one document."""

    max_document_bytes: int = settings.max_upload_size_bytes
    max_pdf_pages: int = settings.max_pdf_pages
    max_pdf_page_pixels: int = settings.max_pdf_page_pixels
    max_pdf_total_pixels: int = settings.max_pdf_total_pixels
    max_pdf_content_stream_bytes: int = settings.max_pdf_content_stream_bytes
    max_pdf_drawing_operations: int = settings.max_pdf_drawing_operations
    ocr_enabled: bool = True
    ocr_language: str = settings.ocr_language
    ocr_dpi: int = settings.ocr_dpi
    ocr_min_text_characters: int = settings.ocr_min_text_characters
    image_dpi: int = 72
    max_visuals_per_page: int = 10
    max_visuals_per_document: int = 500
    chunk_target_characters: int = settings.document_chunk_size_characters
    chunk_overlap_characters: int = settings.document_chunk_overlap_characters
    max_extracted_characters: int = settings.max_extracted_characters
    max_document_chunks: int = settings.max_document_chunks

    def __post_init__(self) -> None:
        positive_options = (
            "max_document_bytes",
            "max_pdf_pages",
            "max_pdf_page_pixels",
            "max_pdf_total_pixels",
            "max_pdf_content_stream_bytes",
            "max_pdf_drawing_operations",
            "ocr_dpi",
            "image_dpi",
            "max_visuals_per_page",
            "max_visuals_per_document",
            "chunk_target_characters",
            "max_extracted_characters",
            "max_document_chunks",
        )
        for option_name in positive_options:
            value = getattr(self, option_name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{option_name} must be a positive integer")

        if type(self.ocr_enabled) is not bool:
            raise ValueError("ocr_enabled must be a boolean")
        if (
            type(self.ocr_min_text_characters) is not int
            or self.ocr_min_text_characters < 0
        ):
            raise ValueError("ocr_min_text_characters must be a non-negative integer")
        if not isinstance(self.ocr_language, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]+(?:\+[A-Za-z0-9_-]+)*", self.ocr_language
        ):
            raise ValueError("ocr_language contains unsupported characters")
        if (
            type(self.chunk_overlap_characters) is not int
            or self.chunk_overlap_characters < 0
            or self.chunk_overlap_characters >= self.chunk_target_characters
        ):
            raise ValueError(
                "chunk_overlap_characters must be non-negative and smaller than target"
            )


@dataclass(frozen=True, slots=True)
class PageText:
    """Clean text associated with a physical page, when pages exist."""

    text: str
    page_number: int | None


class ExtractionMethod(StrEnum):
    """Provenance for text produced by raw document extraction."""

    NATIVE = "native"
    DECODED = "decoded"
    OCR = "ocr"


class VisualType(StrEnum):
    """Coarse visual categories retained for retrieval provenance."""

    DIAGRAM = "diagram"
    TABLE = "table"
    CHART = "chart"
    SCREENSHOT = "screenshot"
    FIGURE = "figure"
    FLOWCHART = "flowchart"
    OTHER = "other"


class VisualSource(StrEnum):
    """PDF primitive that produced a visual candidate."""

    IMAGE = "image"
    TABLE = "table"
    DRAWING = "drawing"


class VisualAnalysisStatus(StrEnum):
    """Outcome for one detected visual region."""

    PENDING = "pending"
    NOT_CONFIGURED = "not_configured"
    SUCCEEDED = "succeeded"
    SKIPPED = "skipped"
    FAILED = "failed"


class PageVisualAnalysisStatus(StrEnum):
    """Aggregate visual-analysis outcome for one page."""

    NOT_APPLICABLE = "not_applicable"
    PENDING = "pending"
    NOT_CONFIGURED = "not_configured"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class OCRStatus(StrEnum):
    """OCR requirement and outcome for one page."""

    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    NO_TEXT = "no_text"


@dataclass(frozen=True, slots=True)
class VisualContent:
    """One selected visual region and its optional semantic description."""

    visual_index: int
    visual_type: VisualType
    source: VisualSource
    bbox: tuple[float, float, float, float]
    description: str | None = None
    analysis_status: VisualAnalysisStatus = VisualAnalysisStatus.PENDING
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class VisualDescription:
    """Validated semantic result returned by a visual provider."""

    visual_type: VisualType
    description: str


@dataclass(frozen=True, slots=True)
class ExtractedPage:
    """One ordered raw content unit before OCR, cleaning, or chunking."""

    content_index: int
    text: str
    page_number: int | None
    extraction_method: ExtractionMethod | None
    has_images: bool
    has_visual_content: bool
    needs_ocr: bool
    visuals: tuple[VisualContent, ...]
    header_candidates: tuple[str, ...] = ()
    footer_candidates: tuple[str, ...] = ()
    text_blocks: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    """Format-independent raw text and provenance for one document."""

    file_type: str
    contents: tuple[ExtractedPage, ...]


@dataclass(frozen=True, slots=True)
class EnrichedPage:
    """Clean merged page text plus raw provenance and structured visuals."""

    content_index: int
    raw_text: str
    text: str
    page_number: int | None
    raw_extraction_method: ExtractionMethod | None
    extraction_method: ExtractionMethod | None
    has_images: bool
    has_visual_content: bool
    needs_ocr: bool
    raw_needs_ocr: bool
    ocr_status: OCRStatus
    visual_analysis_status: PageVisualAnalysisStatus
    visuals: tuple[VisualContent, ...]
    header_candidates: tuple[str, ...]
    footer_candidates: tuple[str, ...]
    text_blocks: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    """A deterministic chunk ready for downstream embedding."""

    chunk_index: int
    text: str
    page_number: int | None
    character_count: int


@dataclass(frozen=True, slots=True)
class DocumentPipelineResult:
    """Clean page-level text and its deterministic chunks."""

    pages: tuple[EnrichedPage, ...]
    chunks: tuple[DocumentChunk, ...]


class StageCallback(Protocol):
    def __call__(self, stage: PipelineStage) -> None: ...


class ExtractionCallback(Protocol):
    def __call__(self, document: ExtractedDocument) -> None: ...


class DocumentExtractor(Protocol):
    """Validate and extract one configured family of document types."""

    def validate(self, content: bytes, options: PipelineOptions) -> str | None: ...

    def extract(
        self,
        file_type: str,
        content: bytes,
        validated_text: str | None,
        options: PipelineOptions,
    ) -> ExtractedDocument: ...


class PDFExtractor:
    def validate(self, content: bytes, options: PipelineOptions) -> None:
        _validate_pdf(content, options)

    def extract(
        self,
        file_type: str,
        content: bytes,
        validated_text: str | None,
        options: PipelineOptions,
    ) -> ExtractedDocument:
        return _extract_pdf_document(file_type, content, options)


class TextExtractor:
    def validate(self, content: bytes, options: PipelineOptions) -> str:
        return _validate_and_decode_text(content)

    def extract(
        self,
        file_type: str,
        content: bytes,
        validated_text: str | None,
        options: PipelineOptions,
    ) -> ExtractedDocument:
        if validated_text is None:
            raise RuntimeError("validated text is required")
        if len(validated_text) > options.max_extracted_characters:
            raise _failure(
                ProcessingErrorCode.EXTRACTED_TEXT_LIMIT_EXCEEDED,
                PipelineStage.EXTRACTING_TEXT,
                retryable=False,
            )
        return ExtractedDocument(
            file_type=file_type,
            contents=(
                ExtractedPage(
                    content_index=0,
                    text=validated_text,
                    page_number=None,
                    extraction_method=ExtractionMethod.DECODED,
                    has_images=False,
                    has_visual_content=False,
                    needs_ocr=False,
                    visuals=(),
                ),
            ),
        )


class OCRProvider(Protocol):
    """Recognize one PDF page without exposing Tesseract to pipeline callers."""

    def extract_text(
        self,
        page: pymupdf.Page,
        *,
        language: str,
        dpi: int,
    ) -> str: ...


class OCRUnavailableError(RuntimeError):
    """The configured OCR implementation is not installed or usable."""


class OCRExecutionError(RuntimeError):
    """The configured OCR implementation failed to recognize a page."""


class TesseractOCRProvider:
    """Local Tesseract OCR implemented through PyMuPDF."""

    def extract_text(
        self,
        page: pymupdf.Page,
        *,
        language: str,
        dpi: int,
    ) -> str:
        try:
            text_page = page.get_textpage_ocr(
                language=language,
                dpi=dpi,
                full=False,
            )
            return page.get_text("text", textpage=text_page)
        except Exception as exc:
            detail = str(exc).lower()
            if "tesseract" in detail or "tessdata" in detail or "ocr support" in detail:
                raise OCRUnavailableError from None
            raise OCRExecutionError from None


class ImageUnderstandingProvider(Protocol):
    """Describe one selected visual region without coupling to a provider."""

    @property
    def enabled(self) -> bool: ...

    def describe_visual(
        self,
        visual_png: bytes,
        *,
        page_number: int,
        visual_index: int,
        suggested_type: VisualType,
    ) -> VisualDescription | None: ...


class TemporaryVisualServiceError(RuntimeError):
    """The visual provider failed transiently and the job should retry."""


class VisualAnalysisError(RuntimeError):
    """One visual cannot be described but other document content can continue."""


class DisabledImageUnderstandingProvider:
    """Explicit no-op provider used when image understanding is disabled."""

    enabled = False

    def describe_visual(
        self,
        visual_png: bytes,
        *,
        page_number: int,
        visual_index: int,
        suggested_type: VisualType,
    ) -> None:
        return None


_DEFAULT_OCR_PROVIDER = TesseractOCRProvider()
_DISABLED_IMAGE_PROVIDER = DisabledImageUnderstandingProvider()
_EXTRACTORS: dict[str, DocumentExtractor] = {
    "pdf": PDFExtractor(),
    "text": TextExtractor(),
}

# MuPDF stores parser warnings globally. Every operation that clears or reads
# the warning buffer must be isolated from other PDF processing threads.
_PDF_WARNING_LOCK = threading.Lock()
_PIPELINE_SEMAPHORE = threading.BoundedSemaphore(
    settings.max_concurrent_document_validations
)

_LEADING_BINARY_SIGNATURES = (
    b"PK\x03\x04",
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"\x7fELF",
)
_PARAGRAPH_BOUNDARY = re.compile(r"\n[ \t]*\n+")
_LINE_BOUNDARY = re.compile(r"\n")
_WORD_BOUNDARY = re.compile(r"[ \t]+")
_HORIZONTAL_WHITESPACE = re.compile(r"[^\S\n]+")
_EXCESSIVE_BLANK_LINES = re.compile(r"\n[ \t]*\n(?:[ \t]*\n)+")
_PDF_PAGE_NUMBER = re.compile(
    r"^(?:page[ \t]+)?[-–—]?[ \t]*(?P<number>\d+)[ \t]*[-–—]?$",
    re.IGNORECASE,
)
_PDF_LIST_ITEM = re.compile(r"^(?:[-+*•]|\d+[.)]|[A-Za-z][.)])(?:[ \t]+|$)")
_PDF_HEADING = re.compile(r"^#{1,6}(?:[ \t]+|$)")
_PDF_TABLE_LINE = re.compile(r"^\|.*\|$")
_PDF_CODE_LINE = re.compile(
    r"^(?:```|~~~|>>>|\.\.\.|(?:def|class|function|SELECT|INSERT|UPDATE|DELETE)\b)"
)
_SENTENCE_END = frozenset(".!?:;。！？")
_REMOVED_FORMAT_CHARACTERS = frozenset({"\u00ad", "\u200b", "\u2060", "\ufeff"})
_PDF_WRAP_MIN_CHARACTERS = 40
_REPEATED_CONTENT_MIN_PAGES = 3
_REPEATED_CONTENT_MIN_RATIO = 0.6
_MAX_VISUAL_DESCRIPTION_CHARACTERS = 2_000
_MIN_VISUAL_DIMENSION_POINTS = 36.0
_MIN_VISUAL_PAGE_AREA_RATIO = 0.01
_VISUAL_SOURCE_PRIORITY = {
    VisualSource.TABLE: 0,
    VisualSource.IMAGE: 1,
    VisualSource.DRAWING: 2,
}


@dataclass(frozen=True, slots=True)
class _VisualCandidate:
    visual: VisualContent
    fingerprint: tuple[int, ...] | None
    page_area_ratio: float


def process_document(
    file_type: str,
    content: bytes,
    *,
    options: PipelineOptions | None = None,
    stage_callback: StageCallback | None = None,
    extraction_callback: ExtractionCallback | None = None,
    ocr_provider: OCRProvider | None = None,
    image_provider: ImageUnderstandingProvider | None = None,
) -> DocumentPipelineResult:
    """Process trusted persisted metadata and bounded document bytes."""
    if not isinstance(file_type, str):
        raise TypeError("file_type must be a string")
    if not isinstance(content, bytes):
        raise TypeError("content must be bytes")

    resolved_options = _resolve_options(options)
    resolved_ocr_provider = ocr_provider or _DEFAULT_OCR_PROVIDER
    resolved_image_provider = image_provider or _DISABLED_IMAGE_PROVIDER
    if type(resolved_image_provider.enabled) is not bool:
        raise TypeError("image provider enabled flag must be a boolean")

    with _PIPELINE_SEMAPHORE:
        return _process_document(
            file_type,
            content,
            options=resolved_options,
            stage_callback=stage_callback,
            extraction_callback=extraction_callback,
            ocr_provider=resolved_ocr_provider,
            image_provider=resolved_image_provider,
        )


def extract_raw_document(
    file_type: str,
    content: bytes,
    *,
    options: PipelineOptions | None = None,
    stage_callback: StageCallback | None = None,
) -> ExtractedDocument:
    """Validate bytes and return raw content before downstream processing."""
    if not isinstance(file_type, str):
        raise TypeError("file_type must be a string")
    if not isinstance(content, bytes):
        raise TypeError("content must be bytes")

    resolved_options = _resolve_options(options)
    with _PIPELINE_SEMAPHORE:
        return _extract_raw_document(
            file_type,
            content,
            options=resolved_options,
            stage_callback=stage_callback,
        )


def _resolve_options(options: PipelineOptions | None) -> PipelineOptions:
    resolved = options or PipelineOptions()
    if not isinstance(resolved, PipelineOptions):
        raise TypeError("options must be PipelineOptions")
    return resolved


def _extract_raw_document(
    file_type: str,
    content: bytes,
    *,
    options: PipelineOptions,
    stage_callback: StageCallback | None,
) -> ExtractedDocument:
    current_stage = PipelineStage.VALIDATING
    try:
        _emit_stage(stage_callback, current_stage)
        type_definition = FILE_TYPES.get(file_type)
        extractor = (
            _EXTRACTORS.get(type_definition["validator"])
            if type_definition is not None
            else None
        )
        if extractor is None:
            raise _failure(
                ProcessingErrorCode.UNSUPPORTED_FILE_TYPE,
                current_stage,
                retryable=False,
            )
        if len(content) > options.max_document_bytes:
            raise _failure(
                ProcessingErrorCode.DOCUMENT_TOO_LARGE,
                current_stage,
                retryable=False,
            )
        if not content:
            raise _failure(
                ProcessingErrorCode.NO_PROCESSABLE_TEXT,
                current_stage,
                retryable=False,
            )

        validated_text = extractor.validate(content, options)
        current_stage = PipelineStage.EXTRACTING_TEXT
        _emit_stage(stage_callback, current_stage)
        extracted_document = extractor.extract(
            file_type,
            content,
            validated_text,
            options,
        )
        if not extracted_document.contents:
            raise _failure(
                ProcessingErrorCode.NO_PROCESSABLE_TEXT,
                current_stage,
                retryable=False,
            )
        if (
            sum(len(content.text) for content in extracted_document.contents)
            > options.max_extracted_characters
        ):
            raise _failure(
                ProcessingErrorCode.EXTRACTED_TEXT_LIMIT_EXCEEDED,
                current_stage,
                retryable=False,
            )
        if type_definition["validator"] != "pdf" and not any(
            content.text.strip() for content in extracted_document.contents
        ):
            raise _failure(
                ProcessingErrorCode.NO_PROCESSABLE_TEXT,
                current_stage,
                retryable=False,
            )
        return extracted_document
    except DocumentProcessingError:
        raise
    except Exception:
        logger.exception(
            "Unexpected raw extraction failure at stage %s", current_stage.value
        )
        raise _failure(
            ProcessingErrorCode.PROCESSING_FAILED,
            current_stage,
            retryable=True,
        ) from None


def _process_document(
    file_type: str,
    content: bytes,
    *,
    options: PipelineOptions,
    stage_callback: StageCallback | None,
    extraction_callback: ExtractionCallback | None,
    ocr_provider: OCRProvider,
    image_provider: ImageUnderstandingProvider,
) -> DocumentPipelineResult:
    current_stage = PipelineStage.EXTRACTING_TEXT
    try:
        extracted_document = _extract_raw_document(
            file_type,
            content,
            options=options,
            stage_callback=stage_callback,
        )
        _emit_extraction(extraction_callback, extracted_document)
        pages = tuple(
            _initial_enriched_page(content) for content in extracted_document.contents
        )

        poor_page_numbers = tuple(
            content.page_number
            for content in extracted_document.contents
            if content.page_number is not None and content.needs_ocr
        )
        if options.ocr_enabled and poor_page_numbers:
            current_stage = PipelineStage.RUNNING_OCR
            _emit_stage(stage_callback, current_stage)
            pages = _apply_ocr(
                content,
                pages,
                poor_page_numbers,
                options=options,
                provider=ocr_provider,
            )

        if file_type == "pdf" and any(page.has_visual_content for page in pages):
            if image_provider.enabled:
                current_stage = PipelineStage.UNDERSTANDING_IMAGES
                _emit_stage(stage_callback, current_stage)
            pages = _apply_visual_understanding(
                content,
                pages,
                options=options,
                provider=image_provider,
            )

        current_stage = PipelineStage.CLEANING_TEXT
        _emit_stage(stage_callback, current_stage)
        try:
            pages = _clean_and_merge_pages(pages, file_type=file_type)
        except Exception:
            logger.exception("Document text cleaning failed")
            raise _failure(
                ProcessingErrorCode.TEXT_CLEANING_FAILED,
                current_stage,
                retryable=True,
            ) from None
        clean_pages = tuple(
            PageText(text=page.text, page_number=page.page_number) for page in pages
        )
        if (
            sum(len(page.text) for page in clean_pages)
            > options.max_extracted_characters
        ):
            raise _failure(
                ProcessingErrorCode.EXTRACTED_TEXT_LIMIT_EXCEEDED,
                current_stage,
                retryable=False,
            )
        if not any(page.text.strip() for page in clean_pages):
            raise _failure(
                ProcessingErrorCode.NO_PROCESSABLE_TEXT,
                current_stage,
                retryable=False,
            )

        current_stage = PipelineStage.CHUNKING
        _emit_stage(stage_callback, current_stage)
        chunks = _chunk_pages(clean_pages, options)
        if not chunks:
            raise _failure(
                ProcessingErrorCode.NO_PROCESSABLE_TEXT,
                current_stage,
                retryable=False,
            )
        return DocumentPipelineResult(pages=pages, chunks=chunks)
    except DocumentProcessingError:
        raise
    except Exception:
        logger.exception(
            "Unexpected document processing failure at stage %s", current_stage.value
        )
        raise _failure(
            ProcessingErrorCode.PROCESSING_FAILED,
            current_stage,
            retryable=True,
        ) from None


def _emit_stage(
    callback: StageCallback | None,
    stage: PipelineStage,
) -> None:
    if callback is None:
        return
    try:
        callback(stage)
    except Exception:
        logger.error("Document stage callback failed at stage %s", stage.value)
        raise _failure(
            ProcessingErrorCode.STAGE_CALLBACK_FAILED,
            stage,
            retryable=True,
        ) from None


def _emit_extraction(
    callback: ExtractionCallback | None,
    document: ExtractedDocument,
) -> None:
    if callback is None:
        return
    try:
        callback(document)
    except Exception:
        logger.error("Raw extraction callback failed")
        raise _failure(
            ProcessingErrorCode.EXTRACTION_CALLBACK_FAILED,
            PipelineStage.EXTRACTING_TEXT,
            retryable=True,
        ) from None


def _failure(
    code: ProcessingErrorCode,
    stage: PipelineStage,
    *,
    retryable: bool,
) -> DocumentProcessingError:
    return DocumentProcessingError(code, stage, retryable=retryable)


def _validate_pdf(content: bytes, options: PipelineOptions) -> None:
    with _PDF_WARNING_LOCK:
        _validate_pdf_locked(content, options)


def _bounded_stream_size(
    pdf: pymupdf.Document,
    content_xref: int,
    remaining_bytes: int,
) -> int:
    if remaining_bytes < 0:
        raise _failure(
            ProcessingErrorCode.DOCUMENT_TOO_COMPLEX,
            PipelineStage.VALIDATING,
            retryable=False,
        )
    raw_stream = pdf.xref_stream_raw(content_xref)
    if not isinstance(raw_stream, bytes):
        raise _failure(
            ProcessingErrorCode.CORRUPTED_PDF,
            PipelineStage.VALIDATING,
            retryable=False,
        )

    filter_type, filter_value = pdf.xref_get_key(content_xref, "Filter")
    if filter_type == "null":
        decoded_size = len(raw_stream)
    elif filter_type == "name" and filter_value in {"/Fl", "/FlateDecode"}:
        try:
            decompressor = zlib.decompressobj()
            decoded = decompressor.decompress(raw_stream, remaining_bytes + 1)
        except zlib.error:
            raise _failure(
                ProcessingErrorCode.CORRUPTED_PDF,
                PipelineStage.VALIDATING,
                retryable=False,
            ) from None
        if len(decoded) > remaining_bytes or decompressor.unconsumed_tail:
            raise _failure(
                ProcessingErrorCode.DOCUMENT_TOO_COMPLEX,
                PipelineStage.VALIDATING,
                retryable=False,
            )
        if not decompressor.eof:
            raise _failure(
                ProcessingErrorCode.CORRUPTED_PDF,
                PipelineStage.VALIDATING,
                retryable=False,
            )
        decoded_size = len(decoded)
    else:
        # Chained or uncommon filters can expand before MuPDF returns control.
        raise _failure(
            ProcessingErrorCode.DOCUMENT_TOO_COMPLEX,
            PipelineStage.VALIDATING,
            retryable=False,
        )

    if decoded_size > remaining_bytes:
        raise _failure(
            ProcessingErrorCode.DOCUMENT_TOO_COMPLEX,
            PipelineStage.VALIDATING,
            retryable=False,
        )
    return decoded_size


def _validate_pdf_locked(content: bytes, options: PipelineOptions) -> None:
    if not content.startswith(b"%PDF-"):
        raise _failure(
            ProcessingErrorCode.CORRUPTED_PDF,
            PipelineStage.VALIDATING,
            retryable=False,
        )

    pymupdf.TOOLS.reset_mupdf_warnings()
    try:
        with pymupdf.open(stream=content, filetype="pdf") as pdf:
            if pdf.needs_pass:
                raise _failure(
                    ProcessingErrorCode.PASSWORD_PROTECTED_PDF,
                    PipelineStage.VALIDATING,
                    retryable=False,
                )
            if pdf.is_repaired:
                raise _failure(
                    ProcessingErrorCode.CORRUPTED_PDF,
                    PipelineStage.VALIDATING,
                    retryable=False,
                )
            if pdf.page_count > options.max_pdf_pages:
                raise _failure(
                    ProcessingErrorCode.DOCUMENT_TOO_COMPLEX,
                    PipelineStage.VALIDATING,
                    retryable=False,
                )

            xref_length = pdf.xref_length()
            total_decoded_stream_bytes = 0
            total_drawing_operations = 0
            image_xrefs: set[int] = set()
            total_image_pixels = 0
            pages = list(pdf)
            page_content_xrefs = [page.get_contents() for page in pages]
            direct_content_xrefs = {
                content_xref
                for content_xrefs in page_content_xrefs
                for content_xref in content_xrefs
            }

            # Preflight every indirect stream before page APIs can recursively
            # process nested Form XObjects, fonts, or object streams.
            for stream_xref in range(1, xref_length):
                if not pdf.xref_is_stream(stream_xref):
                    continue

                subtype_type, subtype_value = pdf.xref_get_key(stream_xref, "Subtype")
                if (
                    subtype_type == "name"
                    and subtype_value == "/Image"
                    and stream_xref not in direct_content_xrefs
                ):
                    width_type, width_value = pdf.xref_get_key(stream_xref, "Width")
                    height_type, height_value = pdf.xref_get_key(stream_xref, "Height")
                    if width_type != "int" or height_type != "int":
                        raise _failure(
                            ProcessingErrorCode.CORRUPTED_PDF,
                            PipelineStage.VALIDATING,
                            retryable=False,
                        )
                    image_pixels = int(width_value) * int(height_value)
                    if image_pixels > options.max_pdf_page_pixels:
                        raise _failure(
                            ProcessingErrorCode.DOCUMENT_TOO_COMPLEX,
                            PipelineStage.VALIDATING,
                            retryable=False,
                        )
                    total_image_pixels += image_pixels
                    if total_image_pixels > options.max_pdf_total_pixels:
                        raise _failure(
                            ProcessingErrorCode.DOCUMENT_TOO_COMPLEX,
                            PipelineStage.VALIDATING,
                            retryable=False,
                        )
                    image_xrefs.add(stream_xref)
                    continue

                remaining_bytes = (
                    options.max_pdf_content_stream_bytes - total_decoded_stream_bytes
                )
                total_decoded_stream_bytes += _bounded_stream_size(
                    pdf,
                    stream_xref,
                    remaining_bytes,
                )

            total_render_pixels = total_image_pixels
            for page, content_xrefs in zip(pages, page_content_xrefs, strict=True):
                image_info = page.get_image_info()
                page_pixels = int(page.rect.width * page.rect.height)
                image_pixels = sum(
                    image["width"] * image["height"]
                    for image in image_info
                    if image.get("xref", 0) not in image_xrefs
                )
                total_render_pixels += page_pixels + image_pixels
                if (
                    page_pixels > options.max_pdf_page_pixels
                    or any(
                        image["width"] * image["height"] > options.max_pdf_page_pixels
                        for image in image_info
                    )
                    or total_render_pixels > options.max_pdf_total_pixels
                ):
                    raise _failure(
                        ProcessingErrorCode.DOCUMENT_TOO_COMPLEX,
                        PipelineStage.VALIDATING,
                        retryable=False,
                    )

                for content_xref in content_xrefs:
                    if (
                        content_xref <= 0
                        or content_xref >= xref_length
                        or not pdf.xref_is_stream(content_xref)
                    ):
                        raise _failure(
                            ProcessingErrorCode.CORRUPTED_PDF,
                            PipelineStage.VALIDATING,
                            retryable=False,
                        )

                drawings = page.get_drawings()
                total_drawing_operations += sum(
                    len(drawing.get("items", ())) for drawing in drawings
                )
                if total_drawing_operations > options.max_pdf_drawing_operations:
                    raise _failure(
                        ProcessingErrorCode.DOCUMENT_TOO_COMPLEX,
                        PipelineStage.VALIDATING,
                        retryable=False,
                    )

                page.get_text("text")
                page.get_pixmap(alpha=False)

            if pymupdf.TOOLS.mupdf_warnings():
                raise _failure(
                    ProcessingErrorCode.CORRUPTED_PDF,
                    PipelineStage.VALIDATING,
                    retryable=False,
                )
    except DocumentProcessingError:
        raise
    except Exception:
        logger.exception("PDF validation failed")
        raise _failure(
            ProcessingErrorCode.CORRUPTED_PDF,
            PipelineStage.VALIDATING,
            retryable=False,
        ) from None
    finally:
        pymupdf.TOOLS.reset_mupdf_warnings()


def _contains_parseable_pdf(content: bytes) -> bool:
    with _PDF_WARNING_LOCK:
        pymupdf.TOOLS.reset_mupdf_warnings()
        try:
            with pymupdf.open(stream=content, filetype="pdf") as pdf:
                return pdf.is_pdf and pdf.page_count >= 0
        except Exception:
            return False
        finally:
            pymupdf.TOOLS.reset_mupdf_warnings()


def _validate_and_decode_text(content: bytes) -> str:
    if _contains_parseable_pdf(content) or content.startswith(
        _LEADING_BINARY_SIGNATURES
    ):
        raise _failure(
            ProcessingErrorCode.CORRUPTED_TEXT,
            PipelineStage.VALIDATING,
            retryable=False,
        )
    try:
        detected = from_bytes(content, enable_fallback=False).best()
    except Exception:
        logger.exception("Text document decoding failed")
        raise _failure(
            ProcessingErrorCode.CORRUPTED_TEXT,
            PipelineStage.VALIDATING,
            retryable=False,
        ) from None
    if detected is None:
        raise _failure(
            ProcessingErrorCode.CORRUPTED_TEXT,
            PipelineStage.VALIDATING,
            retryable=False,
        )

    decoded = str(detected)
    allowed_controls = "\n\r\t\f\u0085"
    if any(
        character not in allowed_controls
        and (ord(character) < 32 or 127 <= ord(character) <= 159)
        for character in decoded
    ):
        raise _failure(
            ProcessingErrorCode.CORRUPTED_TEXT,
            PipelineStage.VALIDATING,
            retryable=False,
        )
    return decoded


def _extract_pdf_document(
    file_type: str,
    content: bytes,
    options: PipelineOptions,
) -> ExtractedDocument:
    with _PDF_WARNING_LOCK:
        pymupdf.TOOLS.reset_mupdf_warnings()
        try:
            with pymupdf.open(stream=content, filetype="pdf") as pdf:
                if pdf.needs_pass:
                    raise _failure(
                        ProcessingErrorCode.PASSWORD_PROTECTED_PDF,
                        PipelineStage.EXTRACTING_TEXT,
                        retryable=False,
                    )
                extracted_values: list[
                    tuple[
                        int,
                        str,
                        int,
                        ExtractionMethod | None,
                        bool,
                        tuple[str, ...],
                        tuple[str, ...],
                        tuple[str, ...],
                    ]
                ] = []
                candidate_pages: list[tuple[list[_VisualCandidate], bool]] = []
                character_count = 0
                for page in pdf:
                    text = page.get_text("text").replace("\x00", "")
                    character_count += len(text)
                    if character_count > options.max_extracted_characters:
                        raise _failure(
                            ProcessingErrorCode.EXTRACTED_TEXT_LIMIT_EXCEEDED,
                            PipelineStage.EXTRACTING_TEXT,
                            retryable=False,
                        )
                    image_info = page.get_image_info()
                    image_xrefs = page.get_images(full=True)
                    for image in image_info:
                        image_bbox = pymupdf.Rect(image.get("bbox"))
                        for image_xref in image_xrefs:
                            if page.get_image_bbox(image_xref) == image_bbox:
                                image["xref"] = image_xref[0]
                                break
                    text_blocks, header_candidates, footer_candidates = (
                        _pdf_layout_content(page)
                    )
                    extracted_values.append(
                        (
                            page.number,
                            text,
                            page.number + 1,
                            ExtractionMethod.NATIVE if text.strip() else None,
                            bool(image_info),
                            header_candidates,
                            footer_candidates,
                            text_blocks,
                        )
                    )
                    candidate_pages.append(
                        _detect_visual_candidates(page, image_info, options)
                    )
                selected_visuals, meaningful_visual_pages = _select_document_visuals(
                    candidate_pages, options
                )
                pages = tuple(
                    ExtractedPage(
                        content_index=content_index,
                        text=text,
                        page_number=page_number,
                        extraction_method=extraction_method,
                        has_images=has_images,
                        has_visual_content=has_visual_content,
                        needs_ocr=(
                            has_visual_content
                            and _text_character_count(text)
                            < options.ocr_min_text_characters
                        ),
                        visuals=visuals,
                        header_candidates=header_candidates,
                        footer_candidates=footer_candidates,
                        text_blocks=text_blocks,
                    )
                    for (
                        content_index,
                        text,
                        page_number,
                        extraction_method,
                        has_images,
                        header_candidates,
                        footer_candidates,
                        text_blocks,
                    ), visuals, has_visual_content in zip(
                        extracted_values,
                        selected_visuals,
                        meaningful_visual_pages,
                        strict=True,
                    )
                )
                if pymupdf.TOOLS.mupdf_warnings():
                    raise _failure(
                        ProcessingErrorCode.CORRUPTED_PDF,
                        PipelineStage.EXTRACTING_TEXT,
                        retryable=False,
                    )
                return ExtractedDocument(file_type=file_type, contents=pages)
        except DocumentProcessingError:
            raise
        except Exception:
            logger.exception("PDF text extraction failed")
            raise _failure(
                ProcessingErrorCode.CORRUPTED_PDF,
                PipelineStage.EXTRACTING_TEXT,
                retryable=False,
            ) from None
        finally:
            pymupdf.TOOLS.reset_mupdf_warnings()


def _text_character_count(text: str) -> int:
    return sum(not character.isspace() for character in text)


def _pdf_layout_content(
    page: pymupdf.Page,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    text_blocks: list[str] = []
    header_lines: list[str] = []
    footer_lines: list[str] = []
    edge_height = min(54.0, page.rect.height * 0.07)
    blocks = page.get_text("blocks", flags=pymupdf.TEXTFLAGS_TEXT, sort=True)
    for block in blocks:
        if len(block) <= 6 or block[6] != 0:
            continue
        text = str(block[4]).strip("\n")
        if not text:
            continue
        text_blocks.append(text)
        lines = tuple(line.strip() for line in text.splitlines() if line.strip())
        if float(block[3]) <= page.rect.y0 + edge_height:
            header_lines.extend(lines)
        if float(block[1]) >= page.rect.y1 - edge_height:
            footer_lines.extend(lines)
    return tuple(text_blocks), tuple(header_lines), tuple(footer_lines)


def _detect_visual_candidates(
    page: pymupdf.Page,
    image_info: list[dict],
    options: PipelineOptions,
) -> tuple[list[_VisualCandidate], bool]:
    page_rect = page.rect
    candidates: list[_VisualCandidate] = []
    candidate_limit = options.max_visuals_per_page * 2
    overflowed = False
    image_fingerprints: set[tuple[int, ...]] = set()

    try:
        tables = page.find_tables().tables
    except Exception:
        logger.exception("Table detection failed on PDF page %s", page.number + 1)
        tables = ()
    for table in tables:
        candidate = _make_visual_candidate(
            table.bbox,
            page_rect,
            visual_type=VisualType.TABLE,
            source=VisualSource.TABLE,
        )
        if candidate is not None:
            overflowed = (
                _retain_visual_candidate(candidates, candidate, candidate_limit)
                or overflowed
            )

    for image in image_info:
        candidate = _make_visual_candidate(
            image.get("bbox"),
            page_rect,
            visual_type=VisualType.FIGURE,
            source=VisualSource.IMAGE,
            fingerprint=_image_fingerprint(image),
        )
        if candidate is not None:
            if (
                candidate.fingerprint is not None
                and candidate.fingerprint in image_fingerprints
            ):
                continue
            if candidate.fingerprint is not None:
                image_fingerprints.add(candidate.fingerprint)
            overflowed = (
                _retain_visual_candidate(candidates, candidate, candidate_limit)
                or overflowed
            )

    try:
        drawing_rects = page.cluster_drawings()
    except Exception:
        logger.exception("Drawing detection failed on PDF page %s", page.number + 1)
        drawing_rects = (page_rect,)
    for drawing_rect in drawing_rects:
        candidate = _make_visual_candidate(
            drawing_rect,
            page_rect,
            visual_type=VisualType.DIAGRAM,
            source=VisualSource.DRAWING,
        )
        if candidate is not None:
            overflowed = (
                _retain_visual_candidate(candidates, candidate, candidate_limit)
                or overflowed
            )

    candidates.sort(
        key=lambda candidate: (
            candidate.visual.bbox[1],
            candidate.visual.bbox[0],
            -candidate.page_area_ratio,
            _VISUAL_SOURCE_PRIORITY[candidate.visual.source],
        )
    )
    selected: list[_VisualCandidate] = []
    for candidate in candidates:
        if any(
            _visual_overlap(candidate.visual, item.visual) >= 0.8 for item in selected
        ):
            continue
        selected.append(candidate)
    return selected, overflowed


def _retain_visual_candidate(
    candidates: list[_VisualCandidate],
    candidate: _VisualCandidate,
    limit: int,
) -> bool:
    if len(candidates) >= limit:
        return True
    candidates.append(candidate)
    return False


def _make_visual_candidate(
    bbox,
    page_rect: pymupdf.Rect,
    *,
    visual_type: VisualType,
    source: VisualSource,
    fingerprint: tuple[int, ...] | None = None,
) -> _VisualCandidate | None:
    try:
        rect = pymupdf.Rect(bbox) & page_rect
    except Exception:
        return None
    if rect.is_empty or rect.width < _MIN_VISUAL_DIMENSION_POINTS:
        return None
    if rect.height < _MIN_VISUAL_DIMENSION_POINTS:
        return None
    page_area = page_rect.width * page_rect.height
    if page_area <= 0:
        return None
    page_area_ratio = rect.width * rect.height / page_area
    if page_area_ratio < _MIN_VISUAL_PAGE_AREA_RATIO:
        return None
    return _VisualCandidate(
        visual=VisualContent(
            visual_index=0,
            visual_type=visual_type,
            source=source,
            bbox=(float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)),
        ),
        fingerprint=fingerprint,
        page_area_ratio=page_area_ratio,
    )


def _image_fingerprint(image: dict) -> tuple[int, ...] | None:
    xref = image.get("xref")
    if type(xref) is not int or xref <= 0:
        return None
    return (xref,)


def _visual_overlap(first: VisualContent, second: VisualContent) -> float:
    first_rect = pymupdf.Rect(first.bbox)
    second_rect = pymupdf.Rect(second.bbox)
    intersection = first_rect & second_rect
    if intersection.is_empty:
        return 0.0
    intersection_area = intersection.width * intersection.height
    smaller_area = min(
        first_rect.width * first_rect.height,
        second_rect.width * second_rect.height,
    )
    return intersection_area / smaller_area if smaller_area else 0.0


def _select_document_visuals(
    candidate_pages: list[tuple[list[_VisualCandidate], bool]],
    options: PipelineOptions,
) -> tuple[tuple[tuple[VisualContent, ...], ...], tuple[bool, ...]]:
    fingerprints = Counter(
        candidate.fingerprint
        for candidates, _overflowed in candidate_pages
        for candidate in candidates
        if candidate.fingerprint is not None
    )
    selected_pages: list[tuple[VisualContent, ...]] = []
    meaningful_visual_pages: list[bool] = []
    selected_total = 0
    for candidates, overflowed in candidate_pages:
        page_visuals: list[VisualContent] = []
        meaningful_candidates = tuple(
            candidate
            for candidate in candidates
            if not (
                candidate.fingerprint is not None
                and fingerprints[candidate.fingerprint] >= 3
                and candidate.page_area_ratio < 0.05
            )
        )
        meaningful_visual_pages.append(bool(meaningful_candidates) or overflowed)
        for candidate in sorted(
            meaningful_candidates,
            key=lambda item: (
                item.visual.bbox[1],
                item.visual.bbox[0],
                _VISUAL_SOURCE_PRIORITY[item.visual.source],
            ),
        ):
            if selected_total >= options.max_visuals_per_document:
                continue
            if len(page_visuals) >= options.max_visuals_per_page:
                continue
            page_visuals.append(
                replace(candidate.visual, visual_index=len(page_visuals))
            )
            selected_total += 1
        selected_pages.append(tuple(page_visuals))
    return tuple(selected_pages), tuple(meaningful_visual_pages)


def _initial_enriched_page(page: ExtractedPage) -> EnrichedPage:
    return EnrichedPage(
        content_index=page.content_index,
        raw_text=page.text,
        text=page.text,
        page_number=page.page_number,
        raw_extraction_method=page.extraction_method,
        extraction_method=page.extraction_method,
        has_images=page.has_images,
        has_visual_content=page.has_visual_content,
        needs_ocr=page.needs_ocr,
        raw_needs_ocr=page.needs_ocr,
        ocr_status=OCRStatus.PENDING if page.needs_ocr else OCRStatus.NOT_REQUIRED,
        visual_analysis_status=(
            PageVisualAnalysisStatus.PENDING
            if page.has_visual_content
            else PageVisualAnalysisStatus.NOT_APPLICABLE
        ),
        visuals=page.visuals,
        header_candidates=page.header_candidates,
        footer_candidates=page.footer_candidates,
        text_blocks=page.text_blocks,
    )


def _apply_ocr(
    content: bytes,
    pages: tuple[EnrichedPage, ...],
    page_numbers: tuple[int, ...],
    *,
    options: PipelineOptions,
    provider: OCRProvider,
) -> tuple[EnrichedPage, ...]:
    _validate_render_budget(
        content,
        page_numbers,
        dpi=options.ocr_dpi,
        options=options,
        stage=PipelineStage.RUNNING_OCR,
    )
    recognized: dict[int, str] = {}
    with _PDF_WARNING_LOCK:
        pymupdf.TOOLS.reset_mupdf_warnings()
        try:
            with pymupdf.open(stream=content, filetype="pdf") as pdf:
                for page_number in page_numbers:
                    page = pdf.load_page(page_number - 1)
                    text = provider.extract_text(
                        page,
                        language=options.ocr_language,
                        dpi=options.ocr_dpi,
                    )
                    if not isinstance(text, str):
                        raise OCRExecutionError
                    recognized[page_number] = text
                if pymupdf.TOOLS.mupdf_warnings():
                    raise OCRExecutionError
        except OCRUnavailableError:
            raise _failure(
                ProcessingErrorCode.OCR_UNAVAILABLE,
                PipelineStage.RUNNING_OCR,
                retryable=False,
            ) from None
        except OCRExecutionError:
            raise _failure(
                ProcessingErrorCode.OCR_FAILED,
                PipelineStage.RUNNING_OCR,
                retryable=True,
            ) from None
        except DocumentProcessingError:
            raise
        except Exception:
            raise _failure(
                ProcessingErrorCode.OCR_FAILED,
                PipelineStage.RUNNING_OCR,
                retryable=True,
            ) from None
        finally:
            pymupdf.TOOLS.reset_mupdf_warnings()

    enriched_pages: list[EnrichedPage] = []
    for page in pages:
        if page.page_number not in recognized:
            enriched_pages.append(page)
            continue
        text = recognized[page.page_number].replace("\x00", "").strip()
        if not _clean_text(text, file_type="pdf", reflow_pdf=False).strip():
            enriched_pages.append(
                replace(
                    page,
                    needs_ocr=False,
                    ocr_status=OCRStatus.NO_TEXT,
                )
            )
            continue
        enriched_pages.append(
            replace(
                page,
                text=text,
                extraction_method=ExtractionMethod.OCR,
                needs_ocr=False,
                ocr_status=OCRStatus.SUCCEEDED,
            )
        )
    return tuple(enriched_pages)


def _apply_visual_understanding(
    content: bytes,
    pages: tuple[EnrichedPage, ...],
    *,
    options: PipelineOptions,
    provider: ImageUnderstandingProvider,
) -> tuple[EnrichedPage, ...]:
    if not provider.enabled:
        return tuple(
            replace(
                page,
                visual_analysis_status=PageVisualAnalysisStatus.NOT_CONFIGURED,
                visuals=tuple(
                    replace(
                        visual,
                        analysis_status=VisualAnalysisStatus.NOT_CONFIGURED,
                    )
                    for visual in page.visuals
                ),
            )
            if page.has_visual_content
            else page
            for page in pages
        )

    _validate_visual_render_budget(pages, options)
    enriched_pages: list[EnrichedPage] = []
    for page in pages:
        if page.page_number is None:
            enriched_pages.append(page)
            continue
        if not page.visuals:
            enriched_pages.append(
                replace(page, visual_analysis_status=PageVisualAnalysisStatus.PARTIAL)
            )
            continue
        analyzed_visuals: list[VisualContent] = []
        for visual in page.visuals:
            visual_png = _render_pdf_visual(
                content,
                page.page_number,
                visual,
                options.image_dpi,
            )
            try:
                result = provider.describe_visual(
                    visual_png,
                    page_number=page.page_number,
                    visual_index=visual.visual_index,
                    suggested_type=visual.visual_type,
                )
            except VisualAnalysisError:
                logger.warning(
                    "Visual analysis failed for PDF page %s visual %s",
                    page.page_number,
                    visual.visual_index,
                )
                analyzed_visuals.append(
                    replace(
                        visual,
                        analysis_status=VisualAnalysisStatus.FAILED,
                        error_code="VISUAL_ANALYSIS_FAILED",
                    )
                )
                continue
            except TemporaryVisualServiceError:
                raise _failure(
                    ProcessingErrorCode.IMAGE_UNDERSTANDING_FAILED,
                    PipelineStage.UNDERSTANDING_IMAGES,
                    retryable=True,
                ) from None
            except Exception:
                logger.exception(
                    "Unexpected visual provider failure on PDF page %s visual %s",
                    page.page_number,
                    visual.visual_index,
                )
                raise _failure(
                    ProcessingErrorCode.IMAGE_UNDERSTANDING_FAILED,
                    PipelineStage.UNDERSTANDING_IMAGES,
                    retryable=True,
                ) from None

            if result is None:
                analyzed_visuals.append(
                    replace(visual, analysis_status=VisualAnalysisStatus.SKIPPED)
                )
                continue
            if (
                not isinstance(result, VisualDescription)
                or not isinstance(result.visual_type, VisualType)
                or not isinstance(result.description, str)
            ):
                analyzed_visuals.append(
                    replace(
                        visual,
                        analysis_status=VisualAnalysisStatus.FAILED,
                        error_code="VISUAL_ANALYSIS_FAILED",
                    )
                )
                continue
            description = result.description.replace("\x00", "").strip()
            if not description or len(description) > _MAX_VISUAL_DESCRIPTION_CHARACTERS:
                analyzed_visuals.append(
                    replace(
                        visual,
                        analysis_status=VisualAnalysisStatus.FAILED,
                        error_code="VISUAL_ANALYSIS_FAILED",
                    )
                )
                continue
            analyzed_visuals.append(
                replace(
                    visual,
                    visual_type=result.visual_type,
                    description=description,
                    analysis_status=VisualAnalysisStatus.SUCCEEDED,
                )
            )
        enriched_pages.append(
            replace(
                page,
                visual_analysis_status=_page_visual_status(analyzed_visuals),
                visuals=tuple(analyzed_visuals),
            )
        )
    return tuple(enriched_pages)


def _page_visual_status(
    visuals: list[VisualContent],
) -> PageVisualAnalysisStatus:
    failed = sum(
        visual.analysis_status == VisualAnalysisStatus.FAILED for visual in visuals
    )
    if failed == len(visuals):
        return PageVisualAnalysisStatus.FAILED
    if failed:
        return PageVisualAnalysisStatus.PARTIAL
    return PageVisualAnalysisStatus.COMPLETED


def _validate_visual_render_budget(
    pages: tuple[EnrichedPage, ...],
    options: PipelineOptions,
) -> None:
    total_pixels = 0
    scale = options.image_dpi / 72
    for page in pages:
        for visual in page.visuals:
            rect = pymupdf.Rect(visual.bbox)
            pixels = max(1, ceil(rect.width * scale)) * max(
                1, ceil(rect.height * scale)
            )
            total_pixels += pixels
            if (
                pixels > options.max_pdf_page_pixels
                or total_pixels > options.max_pdf_total_pixels
            ):
                raise _failure(
                    ProcessingErrorCode.DOCUMENT_TOO_COMPLEX,
                    PipelineStage.UNDERSTANDING_IMAGES,
                    retryable=False,
                )


def _render_pdf_visual(
    content: bytes,
    page_number: int,
    visual: VisualContent,
    dpi: int,
) -> bytes:
    with _PDF_WARNING_LOCK:
        pymupdf.TOOLS.reset_mupdf_warnings()
        try:
            with pymupdf.open(stream=content, filetype="pdf") as pdf:
                page = pdf.load_page(page_number - 1)
                rendered = page.get_pixmap(
                    dpi=dpi,
                    alpha=False,
                    clip=pymupdf.Rect(visual.bbox),
                ).tobytes("png")
                if pymupdf.TOOLS.mupdf_warnings():
                    raise RuntimeError
                return rendered
        except DocumentProcessingError:
            raise
        except Exception:
            raise _failure(
                ProcessingErrorCode.PAGE_RENDER_FAILED,
                PipelineStage.UNDERSTANDING_IMAGES,
                retryable=True,
            ) from None
        finally:
            pymupdf.TOOLS.reset_mupdf_warnings()


def _clean_and_merge_pages(
    pages: tuple[EnrichedPage, ...],
    *,
    file_type: str,
) -> tuple[EnrichedPage, ...]:
    primary_texts = tuple(
        _clean_text(page.text, file_type=file_type, reflow_pdf=False) for page in pages
    )
    repeated_edge_lines = (
        _repeated_pdf_edge_lines(pages, primary_texts) if file_type == "pdf" else set()
    )
    repeated_visuals = _repeated_visual_descriptions(pages, file_type=file_type)

    cleaned_pages: list[EnrichedPage] = []
    seen_repeated_visuals: set[str] = set()
    for page, primary_text in zip(pages, primary_texts, strict=True):
        if file_type == "pdf":
            primary_text, header_candidates, footer_candidates = _pdf_cleaning_source(
                page,
                primary_text,
            )
            primary_text = _remove_pdf_edge_noise(
                primary_text,
                page,
                repeated_edge_lines,
                header_candidates=header_candidates,
                footer_candidates=footer_candidates,
            )
            primary_text = _clean_text(
                primary_text,
                file_type=file_type,
                reflow_pdf=True,
            )

        sections = [primary_text] if primary_text else []
        seen_sections = {_content_key(primary_text)} if primary_text else set()
        for visual in page.visuals:
            if (
                visual.analysis_status != VisualAnalysisStatus.SUCCEEDED
                or visual.description is None
            ):
                continue
            description = _clean_text(visual.description, file_type="pdf")
            description_key = _content_key(description)
            if not description:
                continue
            is_repeated = description_key in repeated_visuals
            is_duplicate = (
                description_key in seen_sections
                or _contains_duplicate_paragraph(primary_text, description)
            )
            if is_repeated and description_key in seen_repeated_visuals:
                continue
            if is_duplicate:
                if is_repeated:
                    seen_repeated_visuals.add(description_key)
                continue
            sections.append(f"[{visual.visual_type.value.title()}]\n{description}")
            seen_sections.add(description_key)
            if is_repeated:
                seen_repeated_visuals.add(description_key)

        cleaned_pages.append(replace(page, text="\n\n".join(sections)))
    return tuple(cleaned_pages)


def _repeated_pdf_edge_lines(
    pages: tuple[EnrichedPage, ...],
    texts: tuple[str, ...],
) -> set[tuple[str, str]]:
    physical_pages = sum(page.page_number is not None for page in pages)
    minimum_count = _repeated_content_minimum(physical_pages)
    if minimum_count is None:
        return set()

    header_pages: dict[str, set[int]] = {}
    footer_pages: dict[str, set[int]] = {}
    for page, text in zip(pages, texts, strict=True):
        if page.page_number is None:
            continue
        _, header_candidates, footer_candidates = _pdf_cleaning_source(page, text)
        for key in {_content_key(line) for line in header_candidates}:
            header_pages.setdefault(key, set()).add(page.page_number)
        for key in {_content_key(line) for line in footer_candidates}:
            footer_pages.setdefault(key, set()).add(page.page_number)

    return {
        (position, key)
        for position, positions in (("header", header_pages), ("footer", footer_pages))
        for key, page_numbers in positions.items()
        if key and len(page_numbers) >= minimum_count
    }


def _repeated_visual_descriptions(
    pages: tuple[EnrichedPage, ...],
    *,
    file_type: str,
) -> set[str]:
    physical_pages = sum(page.page_number is not None for page in pages)
    minimum_count = _repeated_content_minimum(physical_pages)
    if file_type != "pdf" or minimum_count is None:
        return set()

    description_pages: dict[str, set[int]] = {}
    for page in pages:
        if page.page_number is None:
            continue
        for visual in page.visuals:
            if (
                visual.analysis_status == VisualAnalysisStatus.SUCCEEDED
                and visual.description is not None
            ):
                key = _content_key(_clean_text(visual.description, file_type="pdf"))
                if key:
                    description_pages.setdefault(key, set()).add(page.page_number)
    return {
        key
        for key, page_numbers in description_pages.items()
        if len(page_numbers) >= minimum_count
    }


def _repeated_content_minimum(page_count: int) -> int | None:
    if page_count < _REPEATED_CONTENT_MIN_PAGES:
        return None
    return max(
        _REPEATED_CONTENT_MIN_PAGES,
        ceil(page_count * _REPEATED_CONTENT_MIN_RATIO),
    )


def _remove_pdf_edge_noise(
    text: str,
    page: EnrichedPage,
    repeated_lines: set[tuple[str, str]],
    *,
    header_candidates: tuple[str, ...],
    footer_candidates: tuple[str, ...],
) -> str:
    lines = text.splitlines()
    nonempty_indexes = [index for index, line in enumerate(lines) if line.strip()]
    if not nonempty_indexes:
        return text
    header_counts = Counter(
        key
        for candidate in header_candidates
        if (key := _content_key(candidate))
        and (
            ("header", key) in repeated_lines
            or (
                len(nonempty_indexes) > 1
                and _is_pdf_page_number(candidate, page.page_number, is_edge=True)
            )
        )
    )
    footer_counts = Counter(
        key
        for candidate in footer_candidates
        if (key := _content_key(candidate))
        and (
            ("footer", key) in repeated_lines
            or (
                len(nonempty_indexes) > 1
                and _is_pdf_page_number(candidate, page.page_number, is_edge=True)
            )
        )
    )
    for indexes, counts in (
        (nonempty_indexes, header_counts),
        (reversed(nonempty_indexes), footer_counts),
    ):
        for index in indexes:
            key = _content_key(lines[index])
            if counts[key] > 0:
                lines[index] = ""
                counts[key] -= 1
    return "\n".join(lines)


def _pdf_cleaning_source(
    page: EnrichedPage,
    text: str,
) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    if page.extraction_method != ExtractionMethod.OCR:
        block_text = "\n\n".join(page.text_blocks) if page.text_blocks else text
        return block_text, page.header_candidates, page.footer_candidates
    return text, (), ()


def _is_pdf_page_number(
    line: str,
    page_number: int | None,
    *,
    is_edge: bool,
) -> bool:
    if page_number is None or not is_edge:
        return False
    match = _PDF_PAGE_NUMBER.fullmatch(_normalize_horizontal_whitespace(line).strip())
    if match is None:
        return False
    number = match.group("number").lstrip("0") or "0"
    return number == str(page_number)


def _contains_duplicate_paragraph(text: str, candidate: str) -> bool:
    candidate_key = _content_key(candidate)
    return any(
        _content_key(section) == candidate_key
        for paragraph in _PARAGRAPH_BOUNDARY.split(text)
        for section in (paragraph, *paragraph.splitlines())
    )


def _content_key(text: str) -> str:
    return _normalize_horizontal_whitespace(text).strip().casefold()


def _validate_render_budget(
    content: bytes,
    page_numbers: tuple[int, ...],
    *,
    dpi: int,
    options: PipelineOptions,
    stage: PipelineStage,
) -> None:
    with _PDF_WARNING_LOCK:
        pymupdf.TOOLS.reset_mupdf_warnings()
        try:
            total_pixels = 0
            with pymupdf.open(stream=content, filetype="pdf") as pdf:
                for page_number in page_numbers:
                    page = pdf.load_page(page_number - 1)
                    width = max(1, ceil(page.rect.width * dpi / 72))
                    height = max(1, ceil(page.rect.height * dpi / 72))
                    page_pixels = width * height
                    total_pixels += page_pixels
                    if (
                        page_pixels > options.max_pdf_page_pixels
                        or total_pixels > options.max_pdf_total_pixels
                    ):
                        raise _failure(
                            ProcessingErrorCode.DOCUMENT_TOO_COMPLEX,
                            stage,
                            retryable=False,
                        )
                if pymupdf.TOOLS.mupdf_warnings():
                    raise _failure(
                        ProcessingErrorCode.CORRUPTED_PDF,
                        stage,
                        retryable=False,
                    )
        except DocumentProcessingError:
            raise
        except Exception:
            raise _failure(
                ProcessingErrorCode.CORRUPTED_PDF,
                stage,
                retryable=False,
            ) from None
        finally:
            pymupdf.TOOLS.reset_mupdf_warnings()


def _clean_text(text: str, *, file_type: str, reflow_pdf: bool = True) -> str:
    normalized = unicodedata.normalize("NFC", text)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    if file_type == "pdf":
        normalized = re.sub(r"(?<=[^\W\d_])\u00ad\n(?=[^\W\d_])", "", normalized)
    normalized = normalized.replace("\u2029", "\n\n")
    normalized = re.sub(r"[\v\f\u0085\u2028]", "\n", normalized)
    normalized = "".join(
        character
        for character in normalized
        if character in "\n\t"
        or (
            character not in _REMOVED_FORMAT_CHARACTERS
            and unicodedata.category(character) not in {"Cc", "Cs"}
        )
    )
    normalized = normalized.replace("\u00a0", " ").replace("\u00ad", "")
    lines = normalized.split("\n")
    if file_type in {"md", "markdown"}:
        normalized = _clean_markdown_lines(lines)
    elif file_type == "pdf":
        normalized = (
            _clean_pdf_lines(lines)
            if reflow_pdf
            else "\n".join(_clean_pdf_line(line) for line in lines)
        )
    else:
        normalized = "\n".join(_clean_text_line(line) for line in lines)
    if file_type not in {"md", "markdown"}:
        normalized = _remove_garbage_lines(normalized)
    if file_type not in {"md", "markdown"}:
        normalized = _EXCESSIVE_BLANK_LINES.sub("\n\n", normalized)
    return unicodedata.normalize("NFC", normalized.strip("\n"))


def _clean_markdown_lines(lines: list[str]) -> str:
    cleaned: list[str] = []
    fence: tuple[str, int] | None = None
    html_block: str | None = None
    in_indented_code = False
    previous_blank = False
    for line in lines:
        if fence is not None:
            cleaned.append(line)
            if _is_markdown_fence_end(line, fence):
                fence = None
            continue
        if html_block is not None:
            cleaned.append(line)
            if _is_markdown_html_block_end(line, html_block):
                html_block = None
            continue
        html_block = _markdown_html_block_start(line)
        if html_block is not None:
            cleaned.append(line)
            if _is_markdown_html_block_end(line, html_block):
                html_block = None
            previous_blank = False
            continue
        fence = _markdown_fence_start(line)
        if fence is not None:
            cleaned.append(line.rstrip(" \t"))
            previous_blank = False
            continue
        if line.startswith(("    ", "\t")):
            cleaned.append(line)
            in_indented_code = True
            previous_blank = False
            continue
        if not line and in_indented_code:
            cleaned.append(line)
            continue
        in_indented_code = False
        trailing_spaces = len(line) - len(line.rstrip(" "))
        line = line.rstrip(" \t")
        if trailing_spaces >= 2 and line:
            line += "  "
        if not line:
            if previous_blank:
                continue
            previous_blank = True
        else:
            previous_blank = False
        cleaned.append(line)
    return "\n".join(cleaned)


def _markdown_fence_start(line: str) -> tuple[str, int] | None:
    match = re.match(r"^[ ]{0,3}(?P<fence>`{3,}|~{3,})(?P<info>.*)$", line)
    if match is None:
        return None
    marker = match.group("fence")
    if marker[0] == "`" and "`" in match.group("info"):
        return None
    return marker[0], len(marker)


def _is_markdown_fence_end(line: str, fence: tuple[str, int]) -> bool:
    marker, minimum_length = fence
    return (
        re.fullmatch(rf"[ ]{{0,3}}{re.escape(marker)}{{{minimum_length},}}[ \t]*", line)
        is not None
    )


def _markdown_html_block_start(line: str) -> str | None:
    match = re.match(
        r"^[ ]{0,3}<(?P<tag>pre|script|style|textarea)(?:[ \t>]|$)", line, re.I
    )
    return match.group("tag").casefold() if match is not None else None


def _is_markdown_html_block_end(line: str, tag: str) -> bool:
    return re.search(rf"</{re.escape(tag)}[ \t]*>", line, re.I) is not None


def _clean_pdf_lines(lines: list[str]) -> str:
    cleaned = [_clean_pdf_line(line) for line in lines]
    output: list[str] = []
    paragraph: list[str] = []
    previous_line = ""

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            output.append("".join(paragraph))
            paragraph = []

    for line in cleaned:
        if not line:
            flush_paragraph()
            if output and output[-1]:
                output.append("")
            continue
        if not paragraph:
            paragraph = [line]
            previous_line = line
            continue
        if _pdf_lines_are_wrapped(previous_line, line):
            separator = "" if previous_line.rstrip().endswith("-") else " "
            paragraph.append(f"{separator}{line.lstrip()}")
        else:
            flush_paragraph()
            paragraph = [line]
        previous_line = line
    flush_paragraph()
    return "\n".join(output)


def _pdf_lines_are_wrapped(previous: str, current: str) -> bool:
    if _is_protected_pdf_line(previous) or _is_protected_pdf_line(current):
        return False
    previous_stripped = previous.strip()
    current_stripped = current.strip()
    if not previous_stripped or not current_stripped:
        return False
    if previous_stripped[-1] in _SENTENCE_END:
        return False
    return (
        previous_stripped.endswith("-")
        or len(previous_stripped) >= _PDF_WRAP_MIN_CHARACTERS
    ) and _starts_with_lowercase(current_stripped)


def _starts_with_lowercase(text: str) -> bool:
    stripped = text.lstrip()
    return bool(stripped and stripped[0].isalpha() and stripped[0].islower())


def _is_protected_pdf_line(line: str) -> bool:
    return bool(
        _PDF_LIST_ITEM.match(line)
        or _PDF_HEADING.match(line)
        or _PDF_TABLE_LINE.match(line)
        or _PDF_CODE_LINE.match(line)
        or line.startswith(("    ", "\t"))
    )


def _clean_pdf_line(line: str) -> str:
    line = line.replace("\t", "    ").rstrip()
    if _is_protected_pdf_line(line):
        return line
    return _normalize_horizontal_whitespace(line).strip()


def _clean_text_line(line: str) -> str:
    line = line.replace("\t", "    ").rstrip()
    if not line or _is_protected_pdf_line(line):
        return line
    leading_spaces = len(line) - len(line.lstrip(" "))
    return (" " * leading_spaces) + _normalize_horizontal_whitespace(line.lstrip())


def _normalize_horizontal_whitespace(text: str) -> str:
    return _HORIZONTAL_WHITESPACE.sub(" ", text)


def _remove_garbage_lines(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        if _is_garbage_line(line):
            lines.append("")
        else:
            lines.append(line)
    return "\n".join(lines)


def _is_garbage_line(line: str) -> bool:
    visible = [character for character in line if not character.isspace()]
    return bool(visible and all(character == "\ufffd" for character in visible))


def _chunk_pages(
    pages: tuple[PageText, ...],
    options: PipelineOptions,
) -> tuple[DocumentChunk, ...]:
    chunks: list[DocumentChunk] = []
    for page in pages:
        if not page.text.strip():
            continue
        for start, end in _base_chunk_ranges(
            page.text,
            options.chunk_target_characters,
        ):
            chunk_start = _aligned_overlap_start(
                page.text,
                start,
                options.chunk_overlap_characters,
            )
            chunk_text = page.text[chunk_start:end].strip("\n")
            if not chunk_text.strip():
                continue
            chunks.append(
                DocumentChunk(
                    chunk_index=len(chunks),
                    text=chunk_text,
                    page_number=page.page_number,
                    character_count=len(chunk_text),
                )
            )
            if len(chunks) > options.max_document_chunks:
                raise _failure(
                    ProcessingErrorCode.DOCUMENT_CHUNK_LIMIT_EXCEEDED,
                    PipelineStage.CHUNKING,
                    retryable=False,
                )
    return tuple(chunks)


def _base_chunk_ranges(text: str, target: int) -> Iterator[tuple[int, int]]:
    start = 0
    while len(text) - start > target:
        end = _find_chunk_end(text, start, target)
        if end <= start:
            end = min(start + target, len(text))
        yield start, end
        start = end
    if start < len(text):
        yield start, len(text)


def _find_chunk_end(text: str, start: int, target: int) -> int:
    limit = min(start + target, len(text))
    window = text[start:limit]
    minimum = max(1, target // 2)
    for boundary in (_PARAGRAPH_BOUNDARY, _LINE_BOUNDARY, _WORD_BOUNDARY):
        positions = [
            match.end() for match in boundary.finditer(window) if match.end() >= minimum
        ]
        if positions:
            return start + positions[-1]
    return limit


def _aligned_overlap_start(text: str, start: int, overlap: int) -> int:
    if start == 0 or overlap == 0:
        return start
    candidate = max(0, start - overlap)
    if candidate == 0:
        return 0

    window = text[candidate:start]
    for boundary in (_PARAGRAPH_BOUNDARY, _LINE_BOUNDARY, _WORD_BOUNDARY):
        match = boundary.search(window)
        if match is not None and candidate + match.end() < start:
            return candidate + match.end()

    return candidate
