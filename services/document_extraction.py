"""Bounded, deterministic text extraction for validated stored documents."""

import hashlib

import pymupdf
from charset_normalizer import from_bytes

from backend.app.config import settings
from services.document_validation import FILE_TYPES, PDF_OPERATION_LOCK
from services.processing_jobs import ChunkData
from storage.base import Storage, StorageError

MAX_CHUNK_CHARACTERS = 2_000


class DocumentProcessingError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool):
        self.code = code
        self.retryable = retryable
        super().__init__(message)


def extract_document_chunks(
    storage: Storage,
    *,
    storage_provider: str,
    storage_key: str,
    expected_hash: str,
    expected_size: int,
    file_type: str,
) -> list[ChunkData]:
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

    try:
        validator = FILE_TYPES[file_type]["validator"]
    except KeyError as exc:
        raise DocumentProcessingError(
            "UNSUPPORTED_DOCUMENT_TYPE",
            "No extractor is configured for this document type.",
            retryable=False,
        ) from exc

    if validator == "pdf":
        return _extract_pdf(content)
    if validator == "text":
        return _extract_text(content)
    raise DocumentProcessingError(
        "UNSUPPORTED_DOCUMENT_TYPE",
        "No extractor is configured for this document type.",
        retryable=False,
    )


def _extract_text(content: bytes) -> list[ChunkData]:
    detected = from_bytes(content).best()
    if detected is None:
        raise DocumentProcessingError(
            "TEXT_DECODING_FAILED",
            "The document text encoding could not be detected.",
            retryable=False,
        )
    text = str(detected)
    _enforce_character_limit(len(text))
    chunks = _chunk_text(text, page_number=None)
    if not chunks:
        raise DocumentProcessingError(
            "NO_EXTRACTABLE_TEXT",
            "The document did not contain extractable text.",
            retryable=False,
        )
    return chunks


def _extract_pdf(content: bytes) -> list[ChunkData]:
    chunks: list[ChunkData] = []
    extracted_characters = 0
    try:
        with PDF_OPERATION_LOCK, pymupdf.open(stream=content, filetype="pdf") as pdf:
            if pdf.page_count > settings.max_pdf_pages:
                raise DocumentProcessingError(
                    "DOCUMENT_PAGE_LIMIT_EXCEEDED",
                    "The PDF exceeds the processing page limit.",
                    retryable=False,
                )
            for page_number, page in enumerate(pdf, start=1):
                text = page.get_text("text")
                extracted_characters += len(text)
                _enforce_character_limit(extracted_characters)
                chunks.extend(_chunk_text(text, page_number=page_number))
                _enforce_chunk_limit(len(chunks))
    except DocumentProcessingError:
        raise
    except (pymupdf.FileDataError, RuntimeError, ValueError) as exc:
        raise DocumentProcessingError(
            "PDF_EXTRACTION_FAILED",
            "Text extraction failed for the uploaded PDF.",
            retryable=False,
        ) from exc

    if not chunks:
        raise DocumentProcessingError(
            "OCR_REQUIRED",
            "The PDF contains no extractable text and requires OCR.",
            retryable=False,
        )
    return chunks


def _enforce_character_limit(character_count: int) -> None:
    if character_count > settings.max_extracted_characters:
        raise DocumentProcessingError(
            "EXTRACTED_TEXT_LIMIT_EXCEEDED",
            "The document exceeds the extracted text limit.",
            retryable=False,
        )


def _enforce_chunk_limit(chunk_count: int) -> None:
    if chunk_count > settings.max_document_chunks:
        raise DocumentProcessingError(
            "DOCUMENT_CHUNK_LIMIT_EXCEEDED",
            "The document exceeds the processing chunk limit.",
            retryable=False,
        )


def _chunk_text(text: str, *, page_number: int | None) -> list[ChunkData]:
    remaining = text.strip()
    chunks: list[ChunkData] = []
    while remaining:
        if len(remaining) <= MAX_CHUNK_CHARACTERS:
            chunks.append(ChunkData(text=remaining, page_number=page_number))
            break

        split_at = remaining.rfind("\n\n", 0, MAX_CHUNK_CHARACTERS + 1)
        if split_at < MAX_CHUNK_CHARACTERS // 2:
            split_at = remaining.rfind(" ", 0, MAX_CHUNK_CHARACTERS + 1)
        if split_at <= 0:
            split_at = MAX_CHUNK_CHARACTERS

        chunk = remaining[:split_at].strip()
        if chunk:
            chunks.append(ChunkData(text=chunk, page_number=page_number))
            _enforce_chunk_limit(len(chunks))
        remaining = remaining[split_at:].strip()
    return chunks
