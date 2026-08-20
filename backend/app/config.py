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
from urllib.parse import urlsplit

from email_validator import EmailNotValidError, validate_email
from sqlalchemy.engine import make_url
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
STORAGE_BACKEND_S3 = "s3"
STORAGE_BACKENDS = (STORAGE_BACKEND_LOCAL, STORAGE_BACKEND_S3)

AI_PROVIDER_GEMINI = "gemini"
AI_PROVIDER_OLLAMA = "ollama"
RECOGNIZED_AI_PROVIDERS = ("ollama", "openai", "gemini", "claude")
IMPLEMENTED_AI_PROVIDERS = (AI_PROVIDER_GEMINI, AI_PROVIDER_OLLAMA)
DEFAULT_AI_PROVIDER = AI_PROVIDER_OLLAMA
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "llama3.1"
OLLAMA_MODEL_PATTERN = re.compile(r"[A-Za-z0-9._:/-]{1,128}")

IMPLEMENTED_EMBEDDING_PROVIDERS = (AI_PROVIDER_GEMINI, AI_PROVIDER_OLLAMA)
DEFAULT_EMBEDDING_PROVIDER = AI_PROVIDER_OLLAMA
DEFAULT_OLLAMA_EMBEDDING_MODEL = "nomic-embed-text"
DEFAULT_GEMINI_EMBEDDING_MODEL = "text-embedding-004"
DEFAULT_EMBEDDING_BATCH_SIZE = 32
DEFAULT_EMBEDDING_TIMEOUT_SECONDS = 60

IMAGE_PROVIDER_NONE = "none"
IMAGE_PROVIDER_GEMINI = "gemini"
IMAGE_PROVIDER_OLLAMA = "ollama"
RECOGNIZED_IMAGE_PROVIDERS = ("none", "ollama", "openai", "gemini", "claude")
IMPLEMENTED_IMAGE_PROVIDERS = (
    IMAGE_PROVIDER_NONE,
    IMAGE_PROVIDER_GEMINI,
    IMAGE_PROVIDER_OLLAMA,
)
DEFAULT_IMAGE_PROVIDER = IMAGE_PROVIDER_NONE
DEFAULT_OLLAMA_IMAGE_MODEL = "llama3.2-vision"
DEFAULT_GEMINI_IMAGE_MODEL = "gemini-2.5-flash"
DEFAULT_IMAGE_UNDERSTANDING_TIMEOUT_SECONDS = 30
DEFAULT_IMAGE_UNDERSTANDING_MAX_BYTES = 10 * 1024 * 1024

VECTOR_BACKEND_PGVECTOR = "pgvector"
VECTOR_BACKEND_CHROMA = "chroma"
VECTOR_BACKENDS = (VECTOR_BACKEND_PGVECTOR, VECTOR_BACKEND_CHROMA)

DEFAULT_MAX_UPLOAD_SIZE_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_REQUEST_SIZE_BYTES = 1024 * 1024
DEFAULT_MAX_CONCURRENT_DOCUMENT_VALIDATIONS = 2
DEFAULT_UPLOAD_REQUEST_TIMEOUT_SECONDS = 300
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
DEFAULT_MATERIAL_MAX_CHARACTERS = 120_000
DEFAULT_RETRIEVAL_CHUNK_LIMIT = 24
DEFAULT_RETRIEVAL_MIN_SIMILARITY = 0.25
DEFAULT_AI_GENERATION_TIMEOUT_SECONDS = 60
DEFAULT_AI_GENERATION_MAX_ATTEMPTS = 3
DEFAULT_AI_GENERATION_BACKOFF_BASE_SECONDS = 1.0
DEFAULT_AI_GENERATION_BACKOFF_MAX_SECONDS = 10.0
DEFAULT_AI_GENERATION_MAX_CONCURRENCY = 10
DEFAULT_DATABASE_POOL_SIZE = 5
DEFAULT_DATABASE_MAX_OVERFLOW = 5
DEFAULT_DATABASE_POOL_RECYCLE_SECONDS = 900

DEFAULT_CREDIT_INITIAL_GRANT = 50.0
DEFAULT_CREDIT_PERIODIC_GRANT = 50.0
DEFAULT_CREDIT_MAX_BALANCE = 100.0
MAX_CREDIT_BALANCE_CEILING = 1_000_000.0


@dataclass(frozen=True)
class Settings:
    app_env: str
    app_debug: bool

    # Which deployment flavor we are running as - the keystone value.
    deployment_mode: str

    # Structured database connection URL (SQLAlchemy format)
    database_url: str
    database_pool_size: int
    database_max_overflow: int
    database_pool_recycle_seconds: int

    # Where ChromaDB persists its vector data (self-hosted mode only)
    chroma_persist_directory: str

    # Where uploaded files are stored on dist (self-hosted mode only)
    upload_directory: str

    # Provider used for uploaded document content
    storage_backend: str
    storage_namespace: str

    # S3-compatible storage connection (s3 backend only)
    s3_bucket: str | None
    s3_region: str | None
    s3_endpoint_url: str | None
    s3_access_key_id: str | None
    s3_secret_access_key: str | None
    s3_force_path_style: bool

    # Authentication and initial hosted administrator configuration
    jwt_secret_key: str
    bootstrap_admin_email: str | None
    bootstrap_admin_token: str | None

    # AI provider configuration
    ai_provider: str
    gemini_api_key: str | None
    ollama_base_url: str
    ollama_model: str
    ai_fallback_providers: str
    ai_generation_timeout_seconds: int
    ai_generation_max_attempts: int
    ai_generation_backoff_base_seconds: float
    ai_generation_backoff_max_seconds: float
    ai_generation_max_concurrency: int

    # Embedding provider and durable vector storage configuration
    embedding_provider: str
    ollama_embedding_model: str
    gemini_embedding_model: str
    embedding_batch_size: int
    embedding_timeout_seconds: int
    vector_backend: str

    # Visual understanding / image provider configuration
    image_provider: str
    ollama_image_model: str
    gemini_image_model: str
    image_understanding_timeout_seconds: int
    image_understanding_max_bytes: int

    # Maximum accepted document size before content validation
    max_upload_size_bytes: int
    max_request_size_bytes: int
    max_concurrent_document_validations: int
    upload_request_timeout_seconds: int
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
    retrieval_chunk_limit: int
    retrieval_min_similarity: float
    study_guide_material_max_chars: int
    quiz_material_max_chars: int
    flashcard_material_max_chars: int
    ai_tutor_material_max_chars: int
    course_qa_material_max_chars: int

    # Credit lifecycle. See docs/credits.md.
    credit_metering_enabled: bool
    credit_initial_grant: float
    credit_periodic_grant: float
    credit_max_balance: float

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

    storage_backend = os.getenv("STORAGE_BACKEND", STORAGE_BACKEND_LOCAL).strip()
    database_url = load_database_url(mode, app_env=app_env)
    database_pool_size = _bounded_positive_integer_setting(
        "DATABASE_POOL_SIZE", DEFAULT_DATABASE_POOL_SIZE, minimum=1, maximum=20
    )
    database_max_overflow = _nonnegative_integer_setting(
        "DATABASE_MAX_OVERFLOW", DEFAULT_DATABASE_MAX_OVERFLOW
    )
    if database_max_overflow > 20:
        raise ValueError("DATABASE_MAX_OVERFLOW must be at most 20.")
    database_pool_recycle_seconds = _bounded_positive_integer_setting(
        "DATABASE_POOL_RECYCLE_SECONDS",
        DEFAULT_DATABASE_POOL_RECYCLE_SECONDS,
        minimum=60,
        maximum=3600,
    )

    if storage_backend not in STORAGE_BACKENDS:
        raise ValueError(
            f"STORAGE_BACKEND must be one of: {', '.join(STORAGE_BACKENDS)}, "
            f"got: '{storage_backend}'"
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

    if app_env == APP_ENV_PRODUCTION and mode == MODE_HOSTED:
        if storage_backend != STORAGE_BACKEND_S3:
            raise ValueError(
                "Hosted production requires STORAGE_BACKEND='s3' because a single "
                f"instance's local disk cannot qualify as shared storage, got: "
                f"'{storage_backend}'"
            )

    s3_bucket = os.getenv("S3_BUCKET", "").strip() or None
    s3_region = os.getenv("S3_REGION", "").strip() or None
    s3_endpoint_url = os.getenv("S3_ENDPOINT_URL", "").strip() or None
    s3_access_key_id = os.getenv("S3_ACCESS_KEY_ID", "").strip() or None
    s3_secret_access_key = os.getenv("S3_SECRET_ACCESS_KEY", "").strip() or None
    s3_force_path_style = _boolean_setting("S3_FORCE_PATH_STYLE", default=False)
    if storage_backend == STORAGE_BACKEND_S3:
        if not s3_bucket:
            raise ValueError("S3 storage requires S3_BUCKET to be set.")
        if s3_endpoint_url is None and s3_region is None:
            raise ValueError(
                "S3 storage without S3_ENDPOINT_URL requires S3_REGION "
                "to target a real AWS region."
            )
        if s3_endpoint_url is not None:
            endpoint_parts = urlsplit(s3_endpoint_url)
            if (
                endpoint_parts.scheme not in {"http", "https"}
                or not endpoint_parts.hostname
            ):
                raise ValueError(
                    "S3_ENDPOINT_URL must be a valid http:// or https:// URL."
                )
            s3_endpoint_url = s3_endpoint_url.rstrip("/")
        if (s3_access_key_id is None) != (s3_secret_access_key is None):
            raise ValueError(
                "S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY must be set together "
                "when static credentials are used."
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

    ai_provider = (
        os.getenv("AI_PROVIDER", DEFAULT_AI_PROVIDER).strip().lower()
        or DEFAULT_AI_PROVIDER
    )

    if ai_provider not in RECOGNIZED_AI_PROVIDERS:
        raise ValueError(
            f"AI_PROVIDER must be one of: {', '.join(RECOGNIZED_AI_PROVIDERS)}."
        )
    if ai_provider not in IMPLEMENTED_AI_PROVIDERS:
        raise ValueError(
            f"AI_PROVIDER '{ai_provider}' is recognized but not implemented yet. "
            f"Implemented providers: {', '.join(IMPLEMENTED_AI_PROVIDERS)}."
        )
    gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip() or None
    ollama_base_url = _http_url_setting("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL)
    ollama_model = os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL).strip()
    if not OLLAMA_MODEL_PATTERN.fullmatch(ollama_model):
        raise ValueError(
            "OLLAMA_MODEL must contain 1-128 characters limited to letters, digits, "
            "dots, colons, slashes, dashes, or underscores."
        )
    ai_fallback_providers_raw = os.getenv("AI_FALLBACK_PROVIDERS", "").strip()
    if ai_fallback_providers_raw:
        for fallback_token in (
            item.strip().lower()
            for item in ai_fallback_providers_raw.split(",")
            if item.strip()
        ):
            if fallback_token not in RECOGNIZED_AI_PROVIDERS:
                raise ValueError(
                    "AI_FALLBACK_PROVIDERS tokens must be one of: "
                    f"{', '.join(RECOGNIZED_AI_PROVIDERS)}."
                )
            if fallback_token not in IMPLEMENTED_AI_PROVIDERS:
                raise ValueError(
                    f"AI_FALLBACK_PROVIDERS token '{fallback_token}' is recognized but "
                    f"not implemented yet. Implemented providers: "
                    f"{', '.join(IMPLEMENTED_AI_PROVIDERS)}."
                )
    ai_fallback_providers = ai_fallback_providers_raw

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
    upload_request_timeout_seconds = _bounded_positive_integer_setting(
        "UPLOAD_REQUEST_TIMEOUT_SECONDS",
        DEFAULT_UPLOAD_REQUEST_TIMEOUT_SECONDS,
        minimum=1,
        maximum=300,
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

    retrieval_chunk_limit = _bounded_positive_integer_setting(
        "RETRIEVAL_CHUNK_LIMIT",
        DEFAULT_RETRIEVAL_CHUNK_LIMIT,
        minimum=1,
        maximum=200,
    )
    retrieval_min_similarity = _bounded_float_setting(
        "RETRIEVAL_MIN_SIMILARITY",
        DEFAULT_RETRIEVAL_MIN_SIMILARITY,
        minimum=0.0,
        maximum=1.0,
    )

    material_budgets: dict[str, int] = {}
    for name in (
        "STUDY_GUIDE_MATERIAL_MAX_CHARS",
        "QUIZ_MATERIAL_MAX_CHARS",
        "FLASHCARD_MATERIAL_MAX_CHARS",
        "AI_TUTOR_MATERIAL_MAX_CHARS",
        "COURSE_QA_MATERIAL_MAX_CHARS",
    ):
        budget = _positive_integer_setting(name, DEFAULT_MATERIAL_MAX_CHARACTERS)
        if budget < document_chunk_size_characters:
            raise ValueError(
                f"{name} must be at least DOCUMENT_CHUNK_SIZE_CHARACTERS "
                f"({document_chunk_size_characters}) so a single stored chunk "
                "can always fit."
            )
        material_budgets[name] = budget

    ai_generation_timeout_seconds = _bounded_positive_integer_setting(
        "AI_GENERATION_TIMEOUT_SECONDS",
        DEFAULT_AI_GENERATION_TIMEOUT_SECONDS,
        minimum=1,
        maximum=300,
    )
    ai_generation_max_attempts = _bounded_positive_integer_setting(
        "AI_GENERATION_MAX_ATTEMPTS",
        DEFAULT_AI_GENERATION_MAX_ATTEMPTS,
        minimum=1,
        maximum=10,
    )
    ai_generation_backoff_base_seconds = _positive_float_setting(
        "AI_GENERATION_BACKOFF_BASE_SECONDS",
        DEFAULT_AI_GENERATION_BACKOFF_BASE_SECONDS,
    )
    ai_generation_backoff_max_seconds = _positive_float_setting(
        "AI_GENERATION_BACKOFF_MAX_SECONDS",
        DEFAULT_AI_GENERATION_BACKOFF_MAX_SECONDS,
    )
    if ai_generation_backoff_max_seconds < ai_generation_backoff_base_seconds:
        raise ValueError(
            "AI_GENERATION_BACKOFF_MAX_SECONDS must be greater than or equal to "
            "AI_GENERATION_BACKOFF_BASE_SECONDS."
        )
    ai_generation_max_concurrency = _bounded_positive_integer_setting(
        "AI_GENERATION_MAX_CONCURRENCY",
        DEFAULT_AI_GENERATION_MAX_CONCURRENCY,
        minimum=1,
        maximum=100,
    )

    embedding_provider = (
        os.getenv("EMBEDDING_PROVIDER", DEFAULT_EMBEDDING_PROVIDER).strip().lower()
        or DEFAULT_EMBEDDING_PROVIDER
    )
    if embedding_provider not in RECOGNIZED_AI_PROVIDERS:
        raise ValueError(
            f"EMBEDDING_PROVIDER must be one of: {', '.join(RECOGNIZED_AI_PROVIDERS)}."
        )
    if embedding_provider not in IMPLEMENTED_EMBEDDING_PROVIDERS:
        raise ValueError(
            f"EMBEDDING_PROVIDER '{embedding_provider}' is recognized but not "
            "implemented yet. Implemented providers: "
            f"{', '.join(IMPLEMENTED_EMBEDDING_PROVIDERS)}."
        )

    ollama_embedding_model = os.getenv(
        "OLLAMA_EMBEDDING_MODEL",
        DEFAULT_OLLAMA_EMBEDDING_MODEL,
    ).strip()
    if not OLLAMA_MODEL_PATTERN.fullmatch(ollama_embedding_model):
        raise ValueError(
            "OLLAMA_EMBEDDING_MODEL must contain 1-128 characters limited to letters, "
            "digits, dots, colons, slashes, dashes, or underscores."
        )
    gemini_embedding_model = os.getenv(
        "GEMINI_EMBEDDING_MODEL",
        DEFAULT_GEMINI_EMBEDDING_MODEL,
    ).strip()
    if not gemini_embedding_model or len(gemini_embedding_model) > 128:
        raise ValueError(
            "GEMINI_EMBEDDING_MODEL must contain 1-128 non-blank characters."
        )

    embedding_batch_size = _bounded_positive_integer_setting(
        "EMBEDDING_BATCH_SIZE",
        DEFAULT_EMBEDDING_BATCH_SIZE,
        minimum=1,
        maximum=256,
    )
    embedding_timeout_seconds = _bounded_positive_integer_setting(
        "EMBEDDING_TIMEOUT_SECONDS",
        DEFAULT_EMBEDDING_TIMEOUT_SECONDS,
        minimum=1,
        maximum=300,
    )

    image_provider = (
        os.getenv("IMAGE_PROVIDER", DEFAULT_IMAGE_PROVIDER).strip().lower()
        or DEFAULT_IMAGE_PROVIDER
    )
    if image_provider not in RECOGNIZED_IMAGE_PROVIDERS:
        raise ValueError(
            f"IMAGE_PROVIDER must be one of: {', '.join(RECOGNIZED_IMAGE_PROVIDERS)}."
        )
    if image_provider not in IMPLEMENTED_IMAGE_PROVIDERS:
        raise ValueError(
            f"IMAGE_PROVIDER '{image_provider}' is recognized but not "
            "implemented yet. Implemented providers: "
            f"{', '.join(IMPLEMENTED_IMAGE_PROVIDERS)}."
        )

    ollama_image_model = os.getenv(
        "OLLAMA_IMAGE_MODEL",
        DEFAULT_OLLAMA_IMAGE_MODEL,
    ).strip()
    if not OLLAMA_MODEL_PATTERN.fullmatch(ollama_image_model):
        raise ValueError(
            "OLLAMA_IMAGE_MODEL must contain 1-128 characters limited to letters, "
            "digits, dots, colons, slashes, dashes, or underscores."
        )
    gemini_image_model = os.getenv(
        "GEMINI_IMAGE_MODEL",
        DEFAULT_GEMINI_IMAGE_MODEL,
    ).strip()
    if not gemini_image_model or len(gemini_image_model) > 128:
        raise ValueError("GEMINI_IMAGE_MODEL must contain 1-128 non-blank characters.")

    image_understanding_timeout_seconds = _bounded_positive_integer_setting(
        "IMAGE_UNDERSTANDING_TIMEOUT_SECONDS",
        DEFAULT_IMAGE_UNDERSTANDING_TIMEOUT_SECONDS,
        minimum=1,
        maximum=300,
    )
    image_understanding_max_bytes = _bounded_positive_integer_setting(
        "IMAGE_UNDERSTANDING_MAX_BYTES",
        DEFAULT_IMAGE_UNDERSTANDING_MAX_BYTES,
        minimum=1024,
        maximum=50 * 1024 * 1024,
    )

    database_is_postgresql = make_url(database_url).get_backend_name() == "postgresql"
    default_vector_backend = (
        VECTOR_BACKEND_PGVECTOR if database_is_postgresql else VECTOR_BACKEND_CHROMA
    )
    vector_backend = (
        os.getenv("VECTOR_BACKEND", default_vector_backend).strip().lower()
        or default_vector_backend
    )
    if vector_backend not in VECTOR_BACKENDS:
        raise ValueError(
            f"VECTOR_BACKEND must be one of: {', '.join(VECTOR_BACKENDS)}."
        )
    if vector_backend == VECTOR_BACKEND_PGVECTOR and not database_is_postgresql:
        raise ValueError(
            "VECTOR_BACKEND 'pgvector' requires a PostgreSQL DATABASE_URL because the "
            "vector extension exists only in PostgreSQL."
        )

    if app_env == APP_ENV_PRODUCTION:
        if (
            storage_backend == STORAGE_BACKEND_LOCAL
            and not Path(upload_directory).is_absolute()
        ):
            raise ValueError("Production UPLOAD_DIRECTORY must use an absolute path.")
        if (
            vector_backend == VECTOR_BACKEND_CHROMA
            and not Path(chroma_persist_directory).is_absolute()
        ):
            raise ValueError(
                "Production CHROMA_PERSIST_DIRECTORY must use an absolute path."
            )

    credit_metering_enabled = _boolean_setting(
        "CREDIT_METERING_ENABLED",
        default=mode == MODE_HOSTED,
    )
    credit_initial_grant = _nonnegative_float_setting(
        "CREDIT_INITIAL_GRANT",
        DEFAULT_CREDIT_INITIAL_GRANT,
    )
    credit_periodic_grant = _nonnegative_float_setting(
        "CREDIT_PERIODIC_GRANT",
        DEFAULT_CREDIT_PERIODIC_GRANT,
    )
    credit_max_balance = _bounded_float_setting(
        "CREDIT_MAX_BALANCE",
        DEFAULT_CREDIT_MAX_BALANCE,
        minimum=0.0,
        maximum=MAX_CREDIT_BALANCE_CEILING,
    )
    if credit_max_balance < credit_initial_grant:
        raise ValueError("CREDIT_MAX_BALANCE must be at least CREDIT_INITIAL_GRANT.")
    if credit_max_balance < credit_periodic_grant:
        raise ValueError("CREDIT_MAX_BALANCE must be at least CREDIT_PERIODIC_GRANT.")

    return Settings(
        app_env=app_env,
        app_debug=app_debug,
        deployment_mode=mode,
        database_url=database_url,
        database_pool_size=database_pool_size,
        database_max_overflow=database_max_overflow,
        database_pool_recycle_seconds=database_pool_recycle_seconds,
        chroma_persist_directory=chroma_persist_directory,
        upload_directory=upload_directory,
        storage_backend=storage_backend,
        storage_namespace=storage_namespace,
        s3_bucket=s3_bucket,
        s3_region=s3_region,
        s3_endpoint_url=s3_endpoint_url,
        s3_access_key_id=s3_access_key_id,
        s3_secret_access_key=s3_secret_access_key,
        s3_force_path_style=s3_force_path_style,
        jwt_secret_key=jwt_secret_key,
        bootstrap_admin_email=bootstrap_admin_email or None,
        bootstrap_admin_token=bootstrap_admin_token or None,
        ai_provider=ai_provider,
        gemini_api_key=gemini_api_key,
        ollama_base_url=ollama_base_url,
        ollama_model=ollama_model,
        ai_fallback_providers=ai_fallback_providers,
        ai_generation_timeout_seconds=ai_generation_timeout_seconds,
        ai_generation_max_attempts=ai_generation_max_attempts,
        ai_generation_backoff_base_seconds=ai_generation_backoff_base_seconds,
        ai_generation_backoff_max_seconds=ai_generation_backoff_max_seconds,
        ai_generation_max_concurrency=ai_generation_max_concurrency,
        embedding_provider=embedding_provider,
        ollama_embedding_model=ollama_embedding_model,
        gemini_embedding_model=gemini_embedding_model,
        embedding_batch_size=embedding_batch_size,
        embedding_timeout_seconds=embedding_timeout_seconds,
        vector_backend=vector_backend,
        image_provider=image_provider,
        ollama_image_model=ollama_image_model,
        gemini_image_model=gemini_image_model,
        image_understanding_timeout_seconds=image_understanding_timeout_seconds,
        image_understanding_max_bytes=image_understanding_max_bytes,
        max_upload_size_bytes=max_upload_size_bytes,
        max_request_size_bytes=max_request_size_bytes,
        max_concurrent_document_validations=max_concurrent_document_validations,
        upload_request_timeout_seconds=upload_request_timeout_seconds,
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
        retrieval_chunk_limit=retrieval_chunk_limit,
        retrieval_min_similarity=retrieval_min_similarity,
        study_guide_material_max_chars=material_budgets[
            "STUDY_GUIDE_MATERIAL_MAX_CHARS"
        ],
        quiz_material_max_chars=material_budgets["QUIZ_MATERIAL_MAX_CHARS"],
        flashcard_material_max_chars=material_budgets["FLASHCARD_MATERIAL_MAX_CHARS"],
        ai_tutor_material_max_chars=material_budgets["AI_TUTOR_MATERIAL_MAX_CHARS"],
        course_qa_material_max_chars=material_budgets["COURSE_QA_MATERIAL_MAX_CHARS"],
        credit_metering_enabled=credit_metering_enabled,
        credit_initial_grant=credit_initial_grant,
        credit_periodic_grant=credit_periodic_grant,
        credit_max_balance=credit_max_balance,
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


def _http_url_setting(name: str, default: str) -> str:
    raw_value = os.getenv(name, default).strip()
    parts = urlsplit(raw_value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError(f"{name} must be a valid http:// or https:// URL.")
    return raw_value.rstrip("/")


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


def _nonnegative_float_setting(name: str, default: float) -> float:
    raw_value = os.getenv(name, str(default))
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a nonnegative finite number.") from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a nonnegative finite number.")
    return value


def _positive_float_setting(name: str, default: float) -> float:
    raw_value = os.getenv(name, str(default))
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive finite number.") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite number.")
    return value


def _bounded_float_setting(
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    raw_value = os.getenv(name, str(default))
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a finite number.") from exc
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number.")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}.")
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
