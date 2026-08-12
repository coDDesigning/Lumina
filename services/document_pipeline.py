"""Pure document validation, extraction, enrichment, cleaning, and chunking."""

import logging
import re
import threading
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class ExtractedPage:
    """One ordered raw content unit before OCR, cleaning, or chunking."""

    content_index: int
    text: str
    page_number: int | None
    extraction_method: ExtractionMethod | None
    has_images: bool
    needs_ocr: bool


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    """Format-independent raw text and provenance for one document."""

    file_type: str
    contents: tuple[ExtractedPage, ...]


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

    pages: tuple[PageText, ...]
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
                    needs_ocr=False,
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
    """Return a safe text description for a rendered physical PDF page."""

    @property
    def enabled(self) -> bool: ...

    def describe_page(
        self,
        page_png: bytes,
        *,
        page_number: int,
    ) -> str | None: ...


class DisabledImageUnderstandingProvider:
    """Explicit no-op provider used when image understanding is disabled."""

    enabled = False

    def describe_page(
        self,
        page_png: bytes,
        *,
        page_number: int,
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
            PageText(text=content.text, page_number=content.page_number)
            for content in extracted_document.contents
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

        if file_type == "pdf" and image_provider.enabled:
            current_stage = PipelineStage.UNDERSTANDING_IMAGES
            _emit_stage(stage_callback, current_stage)
            pages = _apply_image_understanding(
                content,
                pages,
                options=options,
                provider=image_provider,
            )

        current_stage = PipelineStage.CLEANING_TEXT
        _emit_stage(stage_callback, current_stage)
        clean_pages = tuple(
            PageText(text=_clean_text(page.text), page_number=page.page_number)
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
        return DocumentPipelineResult(pages=clean_pages, chunks=chunks)
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
                pages: list[ExtractedPage] = []
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
                    has_images = bool(page.get_image_info())
                    pages.append(
                        ExtractedPage(
                            content_index=page.number,
                            text=text,
                            page_number=page.number + 1,
                            extraction_method=(
                                ExtractionMethod.NATIVE if text.strip() else None
                            ),
                            has_images=has_images,
                            needs_ocr=(
                                has_images
                                and _text_character_count(text)
                                < options.ocr_min_text_characters
                            ),
                        )
                    )
                if pymupdf.TOOLS.mupdf_warnings():
                    raise _failure(
                        ProcessingErrorCode.CORRUPTED_PDF,
                        PipelineStage.EXTRACTING_TEXT,
                        retryable=False,
                    )
                return ExtractedDocument(file_type=file_type, contents=tuple(pages))
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


def _apply_ocr(
    content: bytes,
    pages: tuple[PageText, ...],
    page_numbers: tuple[int, ...],
    *,
    options: PipelineOptions,
    provider: OCRProvider,
) -> tuple[PageText, ...]:
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

    return tuple(
        PageText(
            text=recognized.get(page.page_number, "").strip() or page.text,
            page_number=page.page_number,
        )
        for page in pages
    )


def _apply_image_understanding(
    content: bytes,
    pages: tuple[PageText, ...],
    *,
    options: PipelineOptions,
    provider: ImageUnderstandingProvider,
) -> tuple[PageText, ...]:
    page_numbers = tuple(
        page.page_number for page in pages if page.page_number is not None
    )
    _validate_render_budget(
        content,
        page_numbers,
        dpi=options.image_dpi,
        options=options,
        stage=PipelineStage.UNDERSTANDING_IMAGES,
    )
    enriched_pages: list[PageText] = []
    for page in pages:
        if page.page_number is None:
            enriched_pages.append(page)
            continue
        page_png = _render_pdf_page(content, page.page_number, options.image_dpi)
        try:
            description = provider.describe_page(
                page_png,
                page_number=page.page_number,
            )
        except Exception:
            raise _failure(
                ProcessingErrorCode.IMAGE_UNDERSTANDING_FAILED,
                PipelineStage.UNDERSTANDING_IMAGES,
                retryable=True,
            ) from None
        if description is not None and not isinstance(description, str):
            raise _failure(
                ProcessingErrorCode.IMAGE_UNDERSTANDING_FAILED,
                PipelineStage.UNDERSTANDING_IMAGES,
                retryable=False,
            )
        if description is None or not description.replace("\x00", "").strip():
            enriched_pages.append(page)
            continue

        separator = "\n\n" if page.text.strip() else ""
        enriched_pages.append(
            PageText(
                text=(
                    f"{page.text.rstrip()}{separator}Image description: {description}"
                ),
                page_number=page.page_number,
            )
        )
    return tuple(enriched_pages)


def _render_pdf_page(content: bytes, page_number: int, dpi: int) -> bytes:
    with _PDF_WARNING_LOCK:
        pymupdf.TOOLS.reset_mupdf_warnings()
        try:
            with pymupdf.open(stream=content, filetype="pdf") as pdf:
                page = pdf.load_page(page_number - 1)
                rendered = page.get_pixmap(dpi=dpi, alpha=False).tobytes("png")
                if pymupdf.TOOLS.mupdf_warnings():
                    raise RuntimeError
                return rendered
        except Exception:
            raise _failure(
                ProcessingErrorCode.CORRUPTED_PDF,
                PipelineStage.UNDERSTANDING_IMAGES,
                retryable=False,
            ) from None
        finally:
            pymupdf.TOOLS.reset_mupdf_warnings()


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
