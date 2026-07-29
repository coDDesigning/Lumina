import hashlib
import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import TypedDict

import pymupdf
from charset_normalizer import from_bytes, is_binary
from fastapi import FastAPI, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.app.config import settings
from schemas.response import BaseResponse

logger = logging.getLogger(__name__)
app = FastAPI()

CONFIG_PATH = Path(__file__).resolve().parents[1] / "app" / "config.json"
MESSAGES_PATH = Path(__file__).resolve().parents[1] / "app" / "messages.json"
UPLOAD_DIRECTORY = Path(settings.upload_directory)

# MuPDF stores parser warnings globally, so concurrent validations must not
# clear or consume each other's warnings.
_PDF_WARNING_LOCK = threading.Lock()


class FileTypeDefinition(TypedDict):
    validator: str
    content_type: str


class ErrorDefinition(TypedDict):
    status_code: int
    code: str
    message: str


class UploadErrorData(BaseModel):
    code: str


class UploadResponse(BaseModel):
    filename: str
    content_type: str
    hash: str


UploadErrorResponse = BaseResponse[UploadErrorData]


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


def _validate_pdf(content: bytes) -> None:
    with _PDF_WARNING_LOCK:
        _validate_pdf_locked(content)


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

            has_document_content = False
            xref_length = pdf.xref_length()

            for page in pdf:
                for content_xref in page.get_contents():
                    if (
                        content_xref <= 0
                        or content_xref >= xref_length
                        or not pdf.xref_is_stream(content_xref)
                    ):
                        raise DocumentValidationError("corrupted_pdf")

                if (
                    page.get_text("text").strip()
                    or page.get_image_info()
                    or page.get_drawings()
                ):
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
    with _PDF_WARNING_LOCK:
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


def validate_document_content(extension: str, content: bytes) -> None:
    """Validate supported document content before it is persisted."""
    if not content:
        raise DocumentValidationError("empty_file")

    try:
        validator_name = FILE_TYPES[extension]["validator"]
        validator = _CONTENT_VALIDATORS[validator_name]
    except KeyError as exc:
        raise RuntimeError(
            f"No validator configured for extension: {extension}"
        ) from exc

    validator(content)


def _store_document(content: bytes, extension: str) -> str:
    hashstr = hashlib.sha256(content).hexdigest()
    UPLOAD_DIRECTORY.mkdir(mode=0o700, parents=True, exist_ok=True)
    storage_path = UPLOAD_DIRECTORY / f"{hashstr}.{extension}"
    temporary_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=UPLOAD_DIRECTORY,
            prefix=f".{hashstr}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        os.replace(temporary_path, storage_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "Failed to remove temporary upload file",
                    exc_info=True,
                )

    return hashstr


def _error_response(error_key: str) -> JSONResponse:
    try:
        error = UPLOAD_ERRORS[error_key]
    except KeyError as exc:
        raise RuntimeError(f"Unknown upload error key: {error_key}") from exc

    response = UploadErrorResponse(
        success=False,
        message=error["message"],
        data=UploadErrorData(code=error["code"]),
    )
    return JSONResponse(status_code=error["status_code"], content=response.model_dump())


@app.exception_handler(RequestValidationError)
async def request_validation_error(
    _request: Request, _exception: RequestValidationError
) -> JSONResponse:
    return _error_response("document_required")


@app.exception_handler(StarletteHTTPException)
async def http_error(
    _request: Request, exception: StarletteHTTPException
) -> JSONResponse:
    if exception.status_code == 400:
        return _error_response("invalid_multipart")

    return JSONResponse(
        status_code=exception.status_code,
        content={"detail": exception.detail},
        headers=exception.headers,
    )


@app.post(
    "/upload-doc",
    response_model=UploadResponse,
    responses={
        400: {"model": UploadErrorResponse, "description": "Invalid upload request"},
        415: {"model": UploadErrorResponse, "description": "Unsupported file type"},
        422: {"model": UploadErrorResponse, "description": "Invalid document"},
        500: {"model": UploadErrorResponse, "description": "Upload storage failure"},
    },
)
async def upload_document(document: UploadFile):
    """Validate and save a document under a content-derived filename."""
    filename = document.filename
    if not filename or not check_file_type(filename):
        return _error_response("unsupported_file_type")

    extension = Path(filename).suffix.lower().lstrip(".")

    try:
        content = await document.read()
        await run_in_threadpool(validate_document_content, extension, content)
    except DocumentValidationError as exc:
        return _error_response(exc.error_key)
    except Exception:
        logger.exception("Failed to read uploaded document")
        return _error_response("upload_failed")

    try:
        hashstr = await run_in_threadpool(_store_document, content, extension)
    except OSError:
        logger.exception("Failed to save uploaded document")
        return _error_response("upload_failed")

    return UploadResponse(
        filename=filename,
        content_type=FILE_TYPES[extension]["content_type"],
        hash=hashstr,
    )
