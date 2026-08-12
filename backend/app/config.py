"""Central configuration for Lumina.

Reads environment variables ONCE at import time, validates them, and
exposes a single `settings` object that the rest of the codebase imports.
No other module should call os.getenv for application configuration -
this file is the single source of truth for "what the environment says".
"""

import math
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

from email_validator import EmailNotValidError, validate_email
from .database_config import (
    APP_ENV_DEVELOPMENT,
    APP_ENV_PRODUCTION,
    APP_ENV_STAGING as APP_ENV_STAGING,
    MODE_HOSTED,
    MODE_SELF_HOSTED,
    load_app_environment,
    load_database_url,
    load_deployment_mode,
)

STORAGE_BACKEND_LOCAL = "local"
DEFAULT_MAX_UPLOAD_SIZE_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_REQUEST_SIZE_BYTES = 1024 * 1024
DEFAULT_MAX_CONCURRENT_DOCUMENT_VALIDATIONS = 2
DEFAULT_MAX_DOCUMENTS_PER_COURSE = 1000
DEFAULT_MAX_COURSE_STORAGE_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_MAX_PDF_PAGES = 500
DEFAULT_MAX_PDF_PAGE_PIXELS = 40_000_000
DEFAULT_MAX_PDF_TOTAL_PIXELS = 100_000_000
DEFAULT_MAX_PDF_CONTENT_STREAM_BYTES = 5 * 1024 * 1024
DEFAULT_MAX_PDF_DRAWING_OPERATIONS = 100_000
DEFAULT_PROCESSING_JOB_LEASE_SECONDS = 60
DEFAULT_PROCESSING_JOB_MAX_ATTEMPTS = 3
DEFAULT_PROCESSING_JOB_POLL_SECONDS = 1.0
DEFAULT_PROCESSING_JOB_ATTEMPT_TIMEOUT_SECONDS = 300
DEFAULT_MAX_EXTRACTED_CHARACTERS = 2_000_000
DEFAULT_MAX_DOCUMENT_CHUNKS = 1_000
DEFAULT_OCR_LANGUAGE = "eng"
DEFAULT_OCR_DPI = 300
DEFAULT_OCR_MIN_TEXT_CHARACTERS = 20
DEFAULT_DOCUMENT_CHUNK_SIZE_CHARACTERS = 1_200
DEFAULT_DOCUMENT_CHUNK_OVERLAP_CHARACTERS = 200


@dataclass(frozen=True)
class Settings:
    app_env: str
    app_debug: bool

    # Which deployment flavor we are running as - the keystone value.
    deployment_mode: str

    # Structured database connection URL (SQLAlchemy format)
    database_url: str

    # Where ChromaDB persists its vector data (self-hosted mode only)
    chroma_persist_directory: str

    # Where uploaded files are stored on dist (self-hosted mode only)
    upload_directory: str

    # Provider used for uploaded document content
    storage_backend: str
    storage_namespace: str

    # Authentication and initial hosted administrator configuration
    jwt_secret_key: str
    bootstrap_admin_email: str | None
    bootstrap_admin_token: str | None

    # Maximum accepted document size before content validation
    max_upload_size_bytes: int
    max_request_size_bytes: int
    max_concurrent_document_validations: int
    max_documents_per_course: int
    max_course_storage_bytes: int
    max_pdf_pages: int
    max_pdf_page_pixels: int
    max_pdf_total_pixels: int
    max_pdf_content_stream_bytes: int
    max_pdf_drawing_operations: int
    processing_job_lease_seconds: int
    processing_job_max_attempts: int
    processing_job_poll_seconds: float
    processing_job_attempt_timeout_seconds: int
    max_extracted_characters: int
    max_document_chunks: int
    ocr_language: str
    ocr_dpi: int
    ocr_min_text_characters: int
    document_chunk_size_characters: int
    document_chunk_overlap_characters: int

    @property
    def is_hosted(self) -> bool:
        return self.deployment_mode == MODE_HOSTED

    @property
    def is_self_hosted(self) -> bool:
        return self.deployment_mode == MODE_SELF_HOSTED

    @property
    def requires_protected_admin_bootstrap(self) -> bool:
        return self.is_hosted or self.app_env == APP_ENV_PRODUCTION


def load_settings() -> Settings:
    """Read, validate and freeze the configuration from the environment.

    Raises ValueError with a human-actionable message if the
    environment is invalid. Failing HERE, at startup, is the whole
    point: a configuration mistake should kill the application in the
    first second with a clear message
    """

    app_env = load_app_environment()
    app_debug = _boolean_setting(
        "APP_DEBUG",
        default=app_env == APP_ENV_DEVELOPMENT,
    )
    if app_env == APP_ENV_PRODUCTION and app_debug:
        raise ValueError("APP_DEBUG must be false in production.")

    mode = load_deployment_mode()
    if app_env == APP_ENV_PRODUCTION and mode == MODE_HOSTED:
        raise ValueError(
            "Hosted production is not supported until durable shared storage "
            "and live PostgreSQL qualification are implemented."
        )
    database_url = load_database_url(mode, app_env=app_env)

    storage_backend = os.getenv("STORAGE_BACKEND", STORAGE_BACKEND_LOCAL)
    if storage_backend != STORAGE_BACKEND_LOCAL:
        raise ValueError(
            f"STORAGE_BACKEND must be '{STORAGE_BACKEND_LOCAL}' because no other "
            f"storage backend is implemented, got: '{storage_backend}'"
        )

    storage_namespace = os.getenv("STORAGE_NAMESPACE", "self-hosted").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,40}", storage_namespace):
        raise ValueError(
            "STORAGE_NAMESPACE must contain 1-40 letters, digits, dots, dashes, or underscores."
        )
    if mode == MODE_HOSTED and "STORAGE_NAMESPACE" not in os.environ:
        raise ValueError(
            "Hosted mode requires STORAGE_NAMESPACE to identify shared storage."
        )

    chroma_persist_directory = os.getenv("CHROMA_PERSIST_DIRECTORY", "./data/chroma")
    upload_directory = os.getenv("UPLOAD_DIRECTORY", "./data/uploads")
    configured_secret = os.getenv("JWT_SECRET_KEY", "").strip()
    if configured_secret and len(configured_secret) < 32:
        raise ValueError("JWT_SECRET_KEY must contain at least 32 characters.")
    if mode == MODE_HOSTED and not configured_secret:
        raise ValueError("Hosted mode requires JWT_SECRET_KEY to be set.")
    if app_env == APP_ENV_PRODUCTION and not configured_secret:
        raise ValueError("Production requires JWT_SECRET_KEY to be set.")
    jwt_secret_key = configured_secret or secrets.token_urlsafe(32)

    bootstrap_admin_email = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
    if bootstrap_admin_email:
        try:
            bootstrap_admin_email = validate_email(
                bootstrap_admin_email,
                check_deliverability=False,
            ).normalized.lower()
        except EmailNotValidError as exc:
            raise ValueError(
                "BOOTSTRAP_ADMIN_EMAIL must be a valid email address."
            ) from exc
    requires_protected_admin_bootstrap = (
        mode == MODE_HOSTED or app_env == APP_ENV_PRODUCTION
    )
    if requires_protected_admin_bootstrap and not bootstrap_admin_email:
        raise ValueError(
            "Hosted mode and production require BOOTSTRAP_ADMIN_EMAIL to be set."
        )

    bootstrap_admin_token = os.getenv("BOOTSTRAP_ADMIN_TOKEN", "").strip()
    token_is_header_safe = all(
        "!" <= character <= "~" for character in bootstrap_admin_token
    )
    if bootstrap_admin_token and (
        len(bootstrap_admin_token) < 32 or not token_is_header_safe
    ):
        raise ValueError(
            "BOOTSTRAP_ADMIN_TOKEN must contain at least 32 visible ASCII characters."
        )
    if requires_protected_admin_bootstrap and not bootstrap_admin_token:
        raise ValueError(
            "Hosted mode and production require BOOTSTRAP_ADMIN_TOKEN to be set."
        )

    if app_env == APP_ENV_PRODUCTION:
        for name, value in (
            ("UPLOAD_DIRECTORY", upload_directory),
            ("CHROMA_PERSIST_DIRECTORY", chroma_persist_directory),
        ):
            if not Path(value).is_absolute():
                raise ValueError(f"Production {name} must use an absolute path.")

    max_upload_size_bytes = _positive_integer_setting(
        "MAX_UPLOAD_SIZE_BYTES",
        DEFAULT_MAX_UPLOAD_SIZE_BYTES,
    )
    max_request_size_bytes = _positive_integer_setting(
        "MAX_REQUEST_SIZE_BYTES",
        DEFAULT_MAX_REQUEST_SIZE_BYTES,
    )
    max_concurrent_document_validations = _positive_integer_setting(
        "MAX_CONCURRENT_DOCUMENT_VALIDATIONS",
        DEFAULT_MAX_CONCURRENT_DOCUMENT_VALIDATIONS,
    )
    max_documents_per_course = _positive_integer_setting(
        "MAX_DOCUMENTS_PER_COURSE",
        DEFAULT_MAX_DOCUMENTS_PER_COURSE,
    )
    max_course_storage_bytes = _positive_integer_setting(
        "MAX_COURSE_STORAGE_BYTES",
        DEFAULT_MAX_COURSE_STORAGE_BYTES,
    )
    max_pdf_pages = _positive_integer_setting(
        "MAX_PDF_PAGES",
        DEFAULT_MAX_PDF_PAGES,
    )
    max_pdf_page_pixels = _positive_integer_setting(
        "MAX_PDF_PAGE_PIXELS",
        DEFAULT_MAX_PDF_PAGE_PIXELS,
    )
    max_pdf_total_pixels = _positive_integer_setting(
        "MAX_PDF_TOTAL_PIXELS",
        DEFAULT_MAX_PDF_TOTAL_PIXELS,
    )
    max_pdf_content_stream_bytes = _positive_integer_setting(
        "MAX_PDF_CONTENT_STREAM_BYTES",
        DEFAULT_MAX_PDF_CONTENT_STREAM_BYTES,
    )
    max_pdf_drawing_operations = _positive_integer_setting(
        "MAX_PDF_DRAWING_OPERATIONS",
        DEFAULT_MAX_PDF_DRAWING_OPERATIONS,
    )
    processing_job_lease_seconds = _bounded_positive_integer_setting(
        "PROCESSING_JOB_LEASE_SECONDS",
        DEFAULT_PROCESSING_JOB_LEASE_SECONDS,
        minimum=5,
        maximum=86_400,
    )
    processing_job_max_attempts = _bounded_positive_integer_setting(
        "PROCESSING_JOB_MAX_ATTEMPTS",
        DEFAULT_PROCESSING_JOB_MAX_ATTEMPTS,
        minimum=1,
        maximum=100,
    )
    processing_job_poll_seconds = _positive_float_setting(
        "PROCESSING_JOB_POLL_SECONDS",
        DEFAULT_PROCESSING_JOB_POLL_SECONDS,
    )
    processing_job_attempt_timeout_seconds = _bounded_positive_integer_setting(
        "PROCESSING_JOB_ATTEMPT_TIMEOUT_SECONDS",
        DEFAULT_PROCESSING_JOB_ATTEMPT_TIMEOUT_SECONDS,
        minimum=1,
        maximum=86_400,
    )
    max_extracted_characters = _positive_integer_setting(
        "MAX_EXTRACTED_CHARACTERS",
        DEFAULT_MAX_EXTRACTED_CHARACTERS,
    )
    max_document_chunks = _positive_integer_setting(
        "MAX_DOCUMENT_CHUNKS",
        DEFAULT_MAX_DOCUMENT_CHUNKS,
    )
    ocr_language = os.getenv("OCR_LANGUAGE", DEFAULT_OCR_LANGUAGE).strip()
    if len(ocr_language) > 128 or not re.fullmatch(
        r"[A-Za-z0-9_-]+(?:\+[A-Za-z0-9_-]+)*", ocr_language
    ):
        raise ValueError(
            "OCR_LANGUAGE must be a 1-128 character expression of safe language "
            "tokens joined by '+'."
        )
    ocr_dpi = _positive_integer_setting("OCR_DPI", DEFAULT_OCR_DPI)
    ocr_min_text_characters = _nonnegative_integer_setting(
        "OCR_MIN_TEXT_CHARACTERS",
        DEFAULT_OCR_MIN_TEXT_CHARACTERS,
    )
    document_chunk_size_characters = _positive_integer_setting(
        "DOCUMENT_CHUNK_SIZE_CHARACTERS",
        DEFAULT_DOCUMENT_CHUNK_SIZE_CHARACTERS,
    )
    document_chunk_overlap_characters = _nonnegative_integer_setting(
        "DOCUMENT_CHUNK_OVERLAP_CHARACTERS",
        DEFAULT_DOCUMENT_CHUNK_OVERLAP_CHARACTERS,
    )
    if document_chunk_overlap_characters >= document_chunk_size_characters:
        raise ValueError(
            "DOCUMENT_CHUNK_OVERLAP_CHARACTERS must be less than "
            "DOCUMENT_CHUNK_SIZE_CHARACTERS."
        )

    return Settings(
        app_env=app_env,
        app_debug=app_debug,
        deployment_mode=mode,
        database_url=database_url,
        chroma_persist_directory=chroma_persist_directory,
        upload_directory=upload_directory,
        storage_backend=storage_backend,
        storage_namespace=storage_namespace,
        jwt_secret_key=jwt_secret_key,
        bootstrap_admin_email=bootstrap_admin_email or None,
        bootstrap_admin_token=bootstrap_admin_token or None,
        max_upload_size_bytes=max_upload_size_bytes,
        max_request_size_bytes=max_request_size_bytes,
        max_concurrent_document_validations=max_concurrent_document_validations,
        max_documents_per_course=max_documents_per_course,
        max_course_storage_bytes=max_course_storage_bytes,
        max_pdf_pages=max_pdf_pages,
        max_pdf_page_pixels=max_pdf_page_pixels,
        max_pdf_total_pixels=max_pdf_total_pixels,
        max_pdf_content_stream_bytes=max_pdf_content_stream_bytes,
        max_pdf_drawing_operations=max_pdf_drawing_operations,
        processing_job_lease_seconds=processing_job_lease_seconds,
        processing_job_max_attempts=processing_job_max_attempts,
        processing_job_poll_seconds=processing_job_poll_seconds,
        processing_job_attempt_timeout_seconds=processing_job_attempt_timeout_seconds,
        max_extracted_characters=max_extracted_characters,
        max_document_chunks=max_document_chunks,
        ocr_language=ocr_language,
        ocr_dpi=ocr_dpi,
        ocr_min_text_characters=ocr_min_text_characters,
        document_chunk_size_characters=document_chunk_size_characters,
        document_chunk_overlap_characters=document_chunk_overlap_characters,
    )


def _positive_integer_setting(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer.") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer.")
    return value


def _nonnegative_integer_setting(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a nonnegative integer.") from exc
    if value < 0:
        raise ValueError(f"{name} must be a nonnegative integer.")
    return value


def _boolean_setting(name: str, *, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false.")


def _positive_float_setting(name: str, default: float) -> float:
    raw_value = os.getenv(name, str(default))
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive finite number.") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite number.")
    return value


def _bounded_positive_integer_setting(
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    value = _positive_integer_setting(name, default)
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}.")
    return value


settings = load_settings()
