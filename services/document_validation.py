"""Configuration-backed validation for uploaded documents."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from fastapi import UploadFile

from backend.app.config import settings

CONFIG_PATH = Path(__file__).resolve().parents[1] / "app" / "config.json"
MESSAGES_PATH = Path(__file__).resolve().parents[1] / "app" / "messages.json"


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


def validate_basic_upload(upload: UploadFile) -> DocumentMetadata:
    """Validate bounded upload metadata without interpreting document content."""
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
