"""Configuration-backed validation for uploaded documents."""

import json
import threading
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

import pymupdf
from charset_normalizer import from_bytes, is_binary
from fastapi import UploadFile

from backend.app.config import settings

CONFIG_PATH = Path(__file__).resolve().parents[1] / "app" / "config.json"
MESSAGES_PATH = Path(__file__).resolve().parents[1] / "app" / "messages.json"

# MuPDF stores parser warnings globally, so concurrent validations must not
# clear or consume each other's warnings.
PDF_OPERATION_LOCK = threading.Lock()
_VALIDATION_SEMAPHORE = threading.BoundedSemaphore(
    settings.max_concurrent_document_validations
)


class FileTypeDefinition(TypedDict):
    validator: str
    content_type: str


class ErrorDefinition(TypedDict):
    status_code: int
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class DocumentMetadata:
    """Trusted metadata captured while validating an uploaded document."""

    original_file_name: str
    file_type: str
    mime_type: str
    file_size: int


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_supported_file_types() -> dict[str, FileTypeDefinition]:
    with CONFIG_PATH.open(encoding="utf-8") as config_file:
        config = json.load(config_file, object_pairs_hook=_unique_json_object)

    if not isinstance(config, dict):
        raise TypeError("Document configuration must be an object")

    configured_types = config.get("supported_file_types")
    if not isinstance(configured_types, dict) or not configured_types:
        raise RuntimeError("supported_file_types must be a non-empty object")

    validated_types: dict[str, FileTypeDefinition] = {}
    for extension, definition in configured_types.items():
        if (
            not isinstance(extension, str)
            or not extension
            or extension != extension.lower().lstrip(".")
            or not isinstance(definition, dict)
        ):
            raise RuntimeError(
                "Each supported file type must use a lowercase extension and object"
            )

        validator = definition.get("validator")
        content_type = definition.get("content_type")
        if (
            not isinstance(validator, str)
            or not validator
            or not isinstance(content_type, str)
            or "/" not in content_type
        ):
            raise RuntimeError(
                f"File type '{extension}' requires a validator and content_type"
            )

        validated_types[extension] = {
            "validator": validator,
            "content_type": content_type,
        }

    return validated_types


def _load_upload_errors() -> dict[str, ErrorDefinition]:
    with MESSAGES_PATH.open(encoding="utf-8") as messages_file:
        catalog = json.load(messages_file, object_pairs_hook=_unique_json_object)

    if not isinstance(catalog, dict):
        raise TypeError("Message catalog must be an object")

    upload_errors = catalog.get("upload_errors")
    if not isinstance(upload_errors, dict) or not upload_errors:
        raise RuntimeError("upload_errors must be a non-empty object")

    validated_errors: dict[str, ErrorDefinition] = {}
    configured_codes: set[str] = set()

    for key, definition in upload_errors.items():
        if not isinstance(key, str) or not key or not isinstance(definition, dict):
            raise RuntimeError("Each upload error must be a named object")

        status_code = definition.get("status_code")
        code = definition.get("code")
        message = definition.get("message")
        if (
            type(status_code) is not int
            or not 400 <= status_code <= 599
            or not isinstance(code, str)
            or not code
            or not isinstance(message, str)
            or not message
        ):
            raise RuntimeError(
                f"Upload error '{key}' requires a 4xx/5xx status_code, code, and message"
            )
        if code in configured_codes:
            raise RuntimeError(f"Duplicate upload error code: {code}")

        configured_codes.add(code)
        validated_errors[key] = {
            "status_code": status_code,
            "code": code,
            "message": message,
        }

    return validated_errors


FILE_TYPES = _load_supported_file_types()
UPLOAD_ERRORS = _load_upload_errors()


class DocumentValidationError(ValueError):
    """An expected validation failure identified by its message-catalog key."""

    def __init__(self, error_key: str):
        if error_key not in UPLOAD_ERRORS:
            raise RuntimeError(f"Unknown upload error key: {error_key}")
        self.error_key = error_key
        super().__init__(error_key)


def check_file_type(filename: str | None) -> bool:
    """Check whether the filename has a configured, supported extension."""
    if not filename:
        return False

    extension = Path(filename).suffix.lower().lstrip(".")
    return bool(extension) and extension in FILE_TYPES


def _derive_file_type_and_mime_type(filename: str | None) -> tuple[str, str]:
    if filename and len(filename) > 255:
        raise DocumentValidationError("invalid_file_name")
    if not filename or not check_file_type(filename):
        raise DocumentValidationError("unsupported_file_type")

    file_type = Path(filename).suffix.lower().lstrip(".")
    return file_type, FILE_TYPES[file_type]["content_type"]


def _validate_pdf(content: bytes) -> None:
    with PDF_OPERATION_LOCK:
        _validate_pdf_locked(content)


def _bounded_stream_size(
    pdf: pymupdf.Document,
    content_xref: int,
    remaining_bytes: int,
) -> int:
    raw_stream = pdf.xref_stream_raw(content_xref)
    if not isinstance(raw_stream, bytes):
        raise DocumentValidationError("corrupted_pdf")

    filter_type, filter_value = pdf.xref_get_key(content_xref, "Filter")
    if filter_type == "null":
        decoded_size = len(raw_stream)
    elif filter_type == "name" and filter_value in {"/Fl", "/FlateDecode"}:
        try:
            decompressor = zlib.decompressobj()
            decoded = decompressor.decompress(raw_stream, remaining_bytes + 1)
        except zlib.error as exc:
            raise DocumentValidationError("corrupted_pdf") from exc
        if len(decoded) > remaining_bytes or decompressor.unconsumed_tail:
            raise DocumentValidationError("document_too_complex")
        if not decompressor.eof:
            raise DocumentValidationError("corrupted_pdf")
        decoded_size = len(decoded)
    else:
        # Chained or uncommon filters can expand before MuPDF returns control.
        raise DocumentValidationError("document_too_complex")

    if decoded_size > remaining_bytes:
        raise DocumentValidationError("document_too_complex")
    return decoded_size


def _validate_pdf_locked(content: bytes) -> None:
    if not content.startswith(b"%PDF-"):
        raise DocumentValidationError("corrupted_pdf")

    pymupdf.TOOLS.reset_mupdf_warnings()
    try:
        with pymupdf.open(stream=content, filetype="pdf") as pdf:
            if pdf.needs_pass:
                raise DocumentValidationError("password_protected_pdf")
            if pdf.is_repaired:
                raise DocumentValidationError("corrupted_pdf")
            if pdf.page_count == 0:
                raise DocumentValidationError("empty_file")
            if pdf.page_count > settings.max_pdf_pages:
                raise DocumentValidationError("document_too_complex")

            has_document_content = False
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
                        raise DocumentValidationError("corrupted_pdf")
                    image_pixels = int(width_value) * int(height_value)
                    if image_pixels > settings.max_pdf_page_pixels:
                        raise DocumentValidationError("document_too_complex")
                    total_image_pixels += image_pixels
                    if total_image_pixels > settings.max_pdf_total_pixels:
                        raise DocumentValidationError("document_too_complex")
                    image_xrefs.add(stream_xref)
                    continue

                remaining_bytes = (
                    settings.max_pdf_content_stream_bytes - total_decoded_stream_bytes
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
                    page_pixels > settings.max_pdf_page_pixels
                    or any(
                        image["width"] * image["height"] > settings.max_pdf_page_pixels
                        for image in image_info
                    )
                    or total_render_pixels > settings.max_pdf_total_pixels
                ):
                    raise DocumentValidationError("document_too_complex")

                for content_xref in content_xrefs:
                    if (
                        content_xref <= 0
                        or content_xref >= xref_length
                        or not pdf.xref_is_stream(content_xref)
                    ):
                        raise DocumentValidationError("corrupted_pdf")

                drawings = page.get_drawings()
                total_drawing_operations += sum(
                    len(drawing.get("items", ())) for drawing in drawings
                )
                if total_drawing_operations > settings.max_pdf_drawing_operations:
                    raise DocumentValidationError("document_too_complex")

                if page.get_text("text").strip() or image_info or drawings:
                    has_document_content = True
                page.get_pixmap(alpha=False)

            if pymupdf.TOOLS.mupdf_warnings():
                raise DocumentValidationError("corrupted_pdf")
            if not has_document_content:
                raise DocumentValidationError("empty_file")
    except DocumentValidationError:
        raise
    except Exception as exc:
        raise DocumentValidationError("corrupted_pdf") from exc
    finally:
        pymupdf.TOOLS.reset_mupdf_warnings()


def _contains_parseable_pdf(content: bytes) -> bool:
    with PDF_OPERATION_LOCK:
        return _contains_parseable_pdf_locked(content)


def _contains_parseable_pdf_locked(content: bytes) -> bool:
    pymupdf.TOOLS.reset_mupdf_warnings()
    try:
        with pymupdf.open(stream=content, filetype="pdf") as pdf:
            return pdf.is_pdf and pdf.page_count >= 0
    except (pymupdf.FileDataError, RuntimeError, ValueError):
        return False
    finally:
        pymupdf.TOOLS.reset_mupdf_warnings()


def _validate_text(content: bytes) -> None:
    leading_binary_signatures = (
        b"PK\x03\x04",
        b"\x89PNG\r\n\x1a\n",
        b"\xff\xd8\xff",
        b"\x7fELF",
    )
    has_binary_signature = _contains_parseable_pdf(content) or content.startswith(
        leading_binary_signatures
    )
    if has_binary_signature or is_binary(content):
        raise DocumentValidationError("corrupted_text")

    detected = from_bytes(content).best()
    if detected is None:
        raise DocumentValidationError("corrupted_text")

    decoded = str(detected)
    if not decoded.strip():
        raise DocumentValidationError("empty_file")

    allowed_controls = "\n\r\t\f"
    invalid_controls = sum(
        character not in allowed_controls
        and (ord(character) < 32 or 127 <= ord(character) <= 159)
        for character in decoded
    )
    if invalid_controls:
        raise DocumentValidationError("corrupted_text")


_CONTENT_VALIDATORS = {
    "pdf": _validate_pdf,
    "text": _validate_text,
}


def _validate_configured_handlers() -> None:
    unknown_validators = {
        definition["validator"] for definition in FILE_TYPES.values()
    } - _CONTENT_VALIDATORS.keys()
    if unknown_validators:
        raise RuntimeError(
            "Unknown configured validators: " + ", ".join(sorted(unknown_validators))
        )


_validate_configured_handlers()


def validate_document_content(file_type: str, content: bytes) -> None:
    """Validate supported document content before it is persisted."""
    if not content:
        raise DocumentValidationError("empty_file")

    try:
        validator_name = FILE_TYPES[file_type]["validator"]
        validator = _CONTENT_VALIDATORS[validator_name]
    except KeyError as exc:
        raise RuntimeError(
            f"No validator configured for extension: {file_type}"
        ) from exc

    validator(content)


def validate_document(upload: UploadFile) -> DocumentMetadata:
    """Validate an upload and return trusted metadata without consuming it."""
    try:
        stream = upload.file
    except Exception as exc:
        raise DocumentValidationError("upload_failed") from exc

    try:
        with _VALIDATION_SEMAPHORE:
            try:
                stream.seek(0)
                filename = upload.filename
                file_type, mime_type = _derive_file_type_and_mime_type(filename)
                content = stream.read(settings.max_upload_size_bytes + 1)
                if not isinstance(content, bytes):
                    raise TypeError("uploaded file stream must return bytes")
                if len(content) > settings.max_upload_size_bytes:
                    raise DocumentValidationError("file_too_large")

                validate_document_content(file_type, content)
                return DocumentMetadata(
                    original_file_name=filename,
                    file_type=file_type,
                    mime_type=mime_type,
                    file_size=len(content),
                )
            except DocumentValidationError:
                raise
            except Exception as exc:
                raise DocumentValidationError("upload_failed") from exc
    finally:
        try:
            stream.seek(0)
        except Exception as exc:
            raise DocumentValidationError("upload_failed") from exc
