"""Pure document validation, extraction, enrichment, cleaning, and chunking."""

import logging
import re
import threading
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


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    """Format-independent raw text and provenance for one document."""

    file_type: str
    contents: tuple[ExtractedPage, ...]


@dataclass(frozen=True, slots=True)
class EnrichedPage:
    """Effective page text plus raw provenance and structured visuals."""

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
        pages = tuple(replace(page, text=_clean_text(page.text)) for page in pages)
        clean_pages = tuple(
            PageText(text=_merge_page_content(page), page_number=page.page_number)
            for page in pages
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
    allowed_controls = "\n\r\t\f"
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
                    tuple[int, str, int, ExtractionMethod | None, bool]
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
                    extracted_values.append(
                        (
                            page.number,
                            text,
                            page.number + 1,
                            ExtractionMethod.NATIVE if text.strip() else None,
                            bool(image_info),
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
                    )
                    for (
                        content_index,
                        text,
                        page_number,
                        extraction_method,
                        has_images,
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
        if not text:
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


def _merge_page_content(page: EnrichedPage) -> str:
    sections = [_clean_text(page.text)] if page.text.strip() else []
    sections.extend(
        f"[{visual.visual_type.value.title()}]\n{_clean_text(visual.description)}"
        for visual in page.visuals
        if visual.analysis_status == VisualAnalysisStatus.SUCCEEDED
        and visual.description is not None
    )
    return "\n\n".join(section for section in sections if section)


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


def _clean_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\x00", "")
    return "\n".join(line.rstrip(" \t") for line in normalized.split("\n")).strip("\n")


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
