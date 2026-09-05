"""Configuration-backed validation for uploaded documents."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from fastapi import UploadFile
from fastapi.responses import JSONResponse

from backend.app.config import settings

CONFIG_PATH = Path(__file__).resolve().parents[1] / "app" / "config.json"
MESSAGES_PATH = Path(__file__).resolve().parents[1] / "app" / "messages.json"

# Content signatures for the validators declared in ``app/config.json``. These
# mirror what the extraction pipeline already enforces (``%PDF-`` at offset 0 in
# ``services.document_pipeline._pdf_preflight``; the PNG/JPEG magic in
# ``_validate_image``) so a byte/extension mismatch is rejected at upload time
# with a clean 415 instead of being persisted and later marked failed by a
# worker. ``text`` has no reliable signature and stays unchecked.
_PDF_MAGIC = b"%PDF-"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"
_IMAGE_MAGIC = (_PNG_MAGIC, _JPEG_MAGIC)


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

    @property
    def code(self) -> str:
        """The stable ``UPLOAD_*`` machine code clients branch on."""
        return UPLOAD_ERRORS[self.error_key]["code"]

    @property
    def status_code(self) -> int:
        """The HTTP status the catalogued error maps to."""
        return UPLOAD_ERRORS[self.error_key]["status_code"]


def upload_error_response(error_key: str) -> JSONResponse:
    """Render a :class:`DocumentValidationError` as the shared upload envelope.

    Both the course-document and profile-document upload routes go through this
    so the two surfaces cannot drift on the status code, the machine code, or
    the envelope shape (the divergence behind P2-008, where the profile route
    read a non-existent ``exc.code`` and 500'd on every rejection).
    """
    error = UPLOAD_ERRORS[error_key]
    return JSONResponse(
        status_code=error["status_code"],
        content={
            "success": False,
            "message": error["message"],
            "data": {"code": error["code"]},
        },
    )


def _content_matches_declared_type(file_type: str, content: bytes) -> bool:
    """Whether the leading bytes are consistent with the declared extension."""
    validator = FILE_TYPES[file_type]["validator"]
    if validator == "pdf":
        return content.startswith(_PDF_MAGIC)
    if validator == "image":
        return content.startswith(_IMAGE_MAGIC)
    return True


def check_file_type(filename: str | None) -> bool:
    """Check whether the filename has a configured, supported extension."""
    if not filename:
        return False

    extension = Path(filename).suffix.lower().lstrip(".")
    return bool(extension) and extension in FILE_TYPES


def _derive_file_type_and_mime_type(filename: str | None) -> tuple[str, str]:
    if filename and (len(filename) > 255 or "\x00" in filename):
        raise DocumentValidationError("invalid_file_name")
    if not filename or not check_file_type(filename):
        raise DocumentValidationError("unsupported_file_type")

    file_type = Path(filename).suffix.lower().lstrip(".")
    return file_type, FILE_TYPES[file_type]["content_type"]


def validate_basic_upload(upload: UploadFile) -> DocumentMetadata:
    """Validate bounded upload metadata and the declared type's magic bytes.

    Content inspection stops at the leading signature: enough to reject an
    obvious extension/byte mismatch (HTML named ``x.pdf``) at the request
    boundary, without parsing the document, which stays the worker's job.
    """
    try:
        stream = upload.file
    except Exception as exc:
        raise DocumentValidationError("upload_failed") from exc

    try:
        try:
            stream.seek(0)
            filename = upload.filename
            file_type, mime_type = _derive_file_type_and_mime_type(filename)
            content = stream.read(settings.max_upload_size_bytes + 1)
            if not isinstance(content, bytes):
                raise TypeError("uploaded file stream must return bytes")
            if len(content) > settings.max_upload_size_bytes:
                raise DocumentValidationError("file_too_large")
            if len(content) == 0:
                raise DocumentValidationError("empty_file")
            if not _content_matches_declared_type(file_type, content):
                raise DocumentValidationError("unsupported_file_type")

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
