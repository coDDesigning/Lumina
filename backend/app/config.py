"""Central configuration for Lumina.

Reads environment variables ONCE at import time, validates them, and
exposes a single `settings` object that the rest of the codebase imports.
No other module should call os.getenv for application configuration -
this file is the single source of truth for "what the environment says".
"""

import json
import logging
import math
import os
import re
import secrets
from ipaddress import IPv4Address, IPv6Address, ip_address
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import idna
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

logger = logging.getLogger(__name__)

STORAGE_BACKEND_LOCAL = "local"
STORAGE_BACKEND_S3 = "s3"
STORAGE_BACKENDS = (STORAGE_BACKEND_LOCAL, STORAGE_BACKEND_S3)

AI_PROVIDER_GEMINI = "gemini"
AI_PROVIDER_OLLAMA = "ollama"
AI_PROVIDER_OPENAI = "openai"
AI_PROVIDER_CLAUDE = "claude"
RECOGNIZED_AI_PROVIDERS = ("ollama", "openai", "gemini", "claude")
IMPLEMENTED_AI_PROVIDERS = (
    AI_PROVIDER_GEMINI,
    AI_PROVIDER_OLLAMA,
    AI_PROVIDER_OPENAI,
    AI_PROVIDER_CLAUDE,
)
DEFAULT_AI_PROVIDER = AI_PROVIDER_OLLAMA
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "llama3.1"
DEFAULT_OLLAMA_TEMPERATURE = 0.2
DEFAULT_OLLAMA_TOP_P = 0.9
DEFAULT_OLLAMA_NUM_CTX = 8192
DEFAULT_OLLAMA_NUM_PREDICT = 4096
DEFAULT_OLLAMA_REPEAT_PENALTY = 1.1
DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"
DEFAULT_OPENAI_MODEL = "gpt-5.6-terra"
DEFAULT_CLAUDE_MODEL = "claude-sonnet-5"
OLLAMA_MODEL_PATTERN = re.compile(r"[A-Za-z0-9._:/-]{1,128}")

IMPLEMENTED_EMBEDDING_PROVIDERS = (AI_PROVIDER_GEMINI, AI_PROVIDER_OLLAMA)
DEFAULT_EMBEDDING_PROVIDER = AI_PROVIDER_OLLAMA
DEFAULT_OLLAMA_EMBEDDING_MODEL = "nomic-embed-text"
DEFAULT_GEMINI_EMBEDDING_MODEL = "gemini-embedding-001"
DEFAULT_EMBEDDING_BATCH_SIZE = 32
DEFAULT_EMBEDDING_TIMEOUT_SECONDS = 60

DEFAULT_COURSE_PURGE_INTERVAL_SECONDS = 3600.0
DEFAULT_EMBEDDING_BACKFILL_INTERVAL_SECONDS = 3600.0
DEFAULT_EMBEDDING_BACKFILL_BATCH_SIZE = 64
DEFAULT_EMBEDDING_BACKFILL_PRUNE_ORPHANS = False
DEFAULT_AI_USAGE_RETENTION_DAYS = 90
DEFAULT_AI_USAGE_CLEANUP_BATCH_SIZE = 1000

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
DEFAULT_PROCESSING_JOB_CONCURRENCY = 2
MAX_PROCESSING_JOB_CONCURRENCY = 6
# A generation is one provider call, not a pipeline, so its lease only has to
# outlive a slow model rather than an extraction. The attempt timeout is the
# ceiling the worker enforces on that call; a reasoning model on a free tier has
# been measured past five minutes, so the default leaves room above it.
DEFAULT_GENERATION_JOB_LEASE_SECONDS = 120
DEFAULT_GENERATION_JOB_MAX_ATTEMPTS = 2
DEFAULT_GENERATION_JOB_POLL_SECONDS = 1.0
DEFAULT_GENERATION_JOB_ATTEMPT_TIMEOUT_SECONDS = 600
DEFAULT_GENERATION_JOB_CONCURRENCY = 2
MAX_GENERATION_JOB_CONCURRENCY = 6
# How many generations one student may have in flight. It bounds what a single
# account can hold of the shared provider quota, so it is a product limit rather
# than a worker tuning knob and is deliberately small.
DEFAULT_GENERATION_JOB_MAX_ACTIVE_PER_USER = 2
MAX_GENERATION_JOB_MAX_ACTIVE_PER_USER = 10
DEFAULT_MAX_EXTRACTED_CHARACTERS = 2_000_000
DEFAULT_MAX_DOCUMENT_CHUNKS = 1_000
DEFAULT_OCR_LANGUAGE = "eng"
DEFAULT_OCR_DPI = 300
DEFAULT_OCR_MIN_TEXT_CHARACTERS = 20
DEFAULT_DOCUMENT_CHUNK_SIZE_CHARACTERS = 1_200
DEFAULT_DOCUMENT_CHUNK_OVERLAP_CHARACTERS = 200
DEFAULT_MATERIAL_MAX_CHARACTERS = 120_000
# A citing feature spends part of its budget on the per-passage citation
# headers, so it reaches less material than it did before citations at the
# same number. Flashcards emit no headers and keep the plain budget.
DEFAULT_CITED_MATERIAL_MAX_CHARACTERS = 126_000
DEFAULT_RETRIEVAL_CHUNK_LIMIT = 24
DEFAULT_RETRIEVAL_MIN_SIMILARITY = 0.25
DEFAULT_AI_GENERATION_TIMEOUT_SECONDS = 60
DEFAULT_AI_GENERATION_MAX_ATTEMPTS = 3
DEFAULT_AI_GENERATION_BACKOFF_BASE_SECONDS = 1.0
DEFAULT_AI_GENERATION_BACKOFF_MAX_SECONDS = 10.0
DEFAULT_AI_GENERATION_MAX_CONCURRENCY = 10
DEFAULT_AI_GENERATION_OVERALL_TIMEOUT_SECONDS = 110
DEFAULT_AI_GRADING_OVERALL_TIMEOUT_SECONDS = 45
MAX_AI_MODEL_COST_RATE_USD_PER_MILLION = 1_000_000.0
MAX_AI_EVENT_ESTIMATED_COST_USD = 1_000_000.0
DEFAULT_DATABASE_POOL_SIZE = 5
DEFAULT_DATABASE_MAX_OVERFLOW = 5
DEFAULT_DATABASE_POOL_RECYCLE_SECONDS = 900

DEFAULT_CREDIT_INITIAL_GRANT = 50.0
DEFAULT_CREDIT_PERIODIC_GRANT = 50.0
DEFAULT_CREDIT_MAX_BALANCE = 100.0
MAX_CREDIT_BALANCE_CEILING = 1_000_000.0

DEFAULT_RATE_LIMIT_LOGIN_MAX_ATTEMPTS = 10
DEFAULT_RATE_LIMIT_LOGIN_WINDOW_SECONDS = 300
DEFAULT_RATE_LIMIT_REGISTER_MAX_ATTEMPTS = 5
DEFAULT_RATE_LIMIT_REGISTER_WINDOW_SECONDS = 3600
DEFAULT_RATE_LIMIT_GENERATION_MAX_ATTEMPTS = 30
DEFAULT_RATE_LIMIT_GENERATION_WINDOW_SECONDS = 3600
DEFAULT_RATE_LIMIT_LOCKOUT_BASE_SECONDS = 30
DEFAULT_RATE_LIMIT_LOCKOUT_MAX_SECONDS = 1800
DEFAULT_RATE_LIMIT_VERIFICATION_MAX_ATTEMPTS = 5
DEFAULT_RATE_LIMIT_VERIFICATION_WINDOW_SECONDS = 3600
DEFAULT_RATE_LIMIT_PASSWORD_RESET_MAX_ATTEMPTS = 5
DEFAULT_RATE_LIMIT_PASSWORD_RESET_WINDOW_SECONDS = 3600

# Authentication hardening. See docs/authentication.md. NIST SP 800-63B puts the
# floor for a user-chosen secret at 8 characters.
DEFAULT_PASSWORD_MIN_LENGTH = 8
# bcrypt truncates at 72 bytes, so a longer minimum could not be enforced.
MAX_PASSWORD_MIN_LENGTH = 64
DEFAULT_EMAIL_VERIFICATION_TOKEN_TTL_HOURS = 24
DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES = 60
DEFAULT_PASSWORD_RESET_TOKEN_TTL_MINUTES = 60
DEFAULT_SMTP_PORT = 587
DEFAULT_SMTP_TIMEOUT_SECONDS = 10
DEFAULT_HSTS_MAX_AGE_SECONDS = 31536000

DEFAULT_ENABLE_HOSTED_ADS = False
DEFAULT_HOSTED_ADS_PROVIDER = "ethicalads"
DEFAULT_HOSTED_ADS_PUBLISHER_ID = "lumina"
DEFAULT_HOSTED_ADS_CSP_ALLOWLIST = (
    "https://media.ethicalads.io",
    "https://server.ethicalads.io",
)


@dataclass(frozen=True)
class Settings:
    app_env: str
    app_debug: bool
    cors_allowed_origins: tuple[str, ...]

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
    ai_model_catalog: dict[str, list[dict[str, object]]]
    ai_pricing_version: str | None
    ai_model_cost_rates: dict[str, dict[str, float]]
    gemini_api_key: str | None
    openai_api_key: str | None
    anthropic_api_key: str | None
    ollama_base_url: str
    ollama_model: str
    ollama_temperature: float
    ollama_top_p: float
    ollama_num_ctx: int
    ollama_num_predict: int
    ollama_repeat_penalty: float
    ai_fallback_providers: str
    ai_generation_timeout_seconds: int
    ai_generation_max_attempts: int
    ai_generation_backoff_base_seconds: float
    ai_generation_backoff_max_seconds: float
    ai_generation_max_concurrency: int
    ai_generation_overall_timeout_seconds: int
    ai_grading_overall_timeout_seconds: int

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
    processing_job_concurrency: int
    generation_job_lease_seconds: int
    generation_job_max_attempts: int
    generation_job_poll_seconds: float
    generation_job_attempt_timeout_seconds: int
    generation_job_concurrency: int
    generation_job_max_active_per_user: int
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
    exam_analysis_material_max_chars: int
    exam_past_paper_max_chars: int
    exam_topic_guide_material_max_chars: int
    exam_topic_summary_material_max_chars: int
    exam_topic_quiz_material_max_chars: int
    exam_similar_questions_material_max_chars: int
    exam_mock_exam_material_max_chars: int
    exam_review_sheet_material_max_chars: int
    exam_mock_exam_question_count: int
    exam_quiz_default_question_count: int

    # Credit lifecycle. See docs/credits.md.
    credit_metering_enabled: bool
    credit_initial_grant: float
    credit_periodic_grant: float
    credit_max_balance: float

    # Abuse controls. See docs/rate_limiting.md.
    rate_limit_login_max_attempts: int
    rate_limit_login_window_seconds: int
    rate_limit_register_max_attempts: int
    rate_limit_register_window_seconds: int
    rate_limit_generation_max_attempts: int
    rate_limit_generation_window_seconds: int
    rate_limit_lockout_base_seconds: int
    rate_limit_lockout_max_seconds: int
    rate_limit_verification_max_attempts: int
    rate_limit_verification_window_seconds: int
    rate_limit_password_reset_max_attempts: int
    rate_limit_password_reset_window_seconds: int

    # Authentication hardening. See docs/authentication.md.
    password_min_length: int
    email_verification_required: bool
    email_verification_token_ttl_hours: int
    access_token_expire_minutes: int
    password_reset_token_ttl_minutes: int
    app_public_base_url: str | None
    email_from_address: str | None
    smtp_host: str | None
    smtp_port: int
    smtp_username: str | None
    smtp_password: str | None
    smtp_use_tls: bool
    smtp_timeout_seconds: int
    security_headers_enabled: bool
    hsts_enabled: bool
    hsts_max_age_seconds: int

    # Periodic maintenance configuration
    course_purge_interval_seconds: float
    embedding_backfill_interval_seconds: float
    embedding_backfill_batch_size: int
    embedding_backfill_prune_orphans: bool
    ai_usage_retention_days: int
    ai_usage_cleanup_batch_size: int

    # Optional hosted advertising configuration
    enable_hosted_ads: bool
    hosted_ads_provider: str | None
    hosted_ads_publisher_id: str | None

    @property
    def is_hosted(self) -> bool:
        return self.deployment_mode == MODE_HOSTED

    @property
    def is_self_hosted(self) -> bool:
        return self.deployment_mode == MODE_SELF_HOSTED

    @property
    def requires_protected_admin_bootstrap(self) -> bool:
        return self.is_hosted or self.app_env == APP_ENV_PRODUCTION

    @property
    def email_delivery_configured(self) -> bool:
        """Whether outbound mail has somewhere to go and a return address."""
        return bool(
            self.smtp_host and self.email_from_address and self.app_public_base_url
        )


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
    cors_allowed_origins = _cors_allowed_origins_setting()

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
    openai_api_key = os.getenv("OPENAI_API_KEY", "").strip() or None
    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", "").strip() or None

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

    def _require_key(provider: str, key: str | None, env_name: str) -> None:
        if ai_provider == provider or (
            ai_fallback_providers_raw and provider in ai_fallback_providers_raw
        ):
            if not key:
                raise ValueError(
                    f"{env_name} is required when {provider} is used as primary or fallback provider."
                )

    _require_key(AI_PROVIDER_GEMINI, gemini_api_key, "GEMINI_API_KEY")
    _require_key(AI_PROVIDER_OPENAI, openai_api_key, "OPENAI_API_KEY")
    _require_key(AI_PROVIDER_CLAUDE, anthropic_api_key, "ANTHROPIC_API_KEY")

    ollama_base_url = _http_url_setting("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL)
    ollama_model = os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL).strip()
    if not OLLAMA_MODEL_PATTERN.fullmatch(ollama_model):
        raise ValueError(
            "OLLAMA_MODEL must contain 1-128 characters limited to letters, digits, "
            "dots, colons, slashes, dashes, or underscores."
        )
    ollama_temperature = _bounded_float_setting(
        "OLLAMA_TEMPERATURE",
        DEFAULT_OLLAMA_TEMPERATURE,
        minimum=0.0,
        maximum=2.0,
    )
    ollama_top_p = _bounded_float_setting(
        "OLLAMA_TOP_P",
        DEFAULT_OLLAMA_TOP_P,
        minimum=0.01,
        maximum=1.0,
    )
    ollama_num_ctx = _bounded_positive_integer_setting(
        "OLLAMA_NUM_CTX",
        DEFAULT_OLLAMA_NUM_CTX,
        minimum=512,
        maximum=131_072,
    )
    ollama_num_predict = _bounded_positive_integer_setting(
        "OLLAMA_NUM_PREDICT",
        DEFAULT_OLLAMA_NUM_PREDICT,
        minimum=64,
        maximum=131_072,
    )
    ollama_repeat_penalty = _bounded_float_setting(
        "OLLAMA_REPEAT_PENALTY",
        DEFAULT_OLLAMA_REPEAT_PENALTY,
        minimum=0.5,
        maximum=2.0,
    )
    if ollama_num_predict > ollama_num_ctx:
        raise ValueError(
            "OLLAMA_NUM_PREDICT must not exceed OLLAMA_NUM_CTX; the response shares "
            f"the context window with the prompt (got {ollama_num_predict} > {ollama_num_ctx})."
        )
    ai_model_catalog = _ai_model_catalog_setting(ollama_model, ollama_num_ctx)
    ai_pricing_version, ai_model_cost_rates = _ai_model_cost_rates_setting()

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
    processing_job_concurrency = _bounded_positive_integer_setting(
        "PROCESSING_JOB_CONCURRENCY",
        DEFAULT_PROCESSING_JOB_CONCURRENCY,
        minimum=1,
        maximum=MAX_PROCESSING_JOB_CONCURRENCY,
    )
    generation_job_lease_seconds = _bounded_positive_integer_setting(
        "GENERATION_JOB_LEASE_SECONDS",
        DEFAULT_GENERATION_JOB_LEASE_SECONDS,
        minimum=5,
        maximum=86_400,
    )
    generation_job_max_attempts = _bounded_positive_integer_setting(
        "GENERATION_JOB_MAX_ATTEMPTS",
        DEFAULT_GENERATION_JOB_MAX_ATTEMPTS,
        minimum=1,
        maximum=100,
    )
    generation_job_poll_seconds = _positive_float_setting(
        "GENERATION_JOB_POLL_SECONDS",
        DEFAULT_GENERATION_JOB_POLL_SECONDS,
    )
    generation_job_attempt_timeout_seconds = _bounded_positive_integer_setting(
        "GENERATION_JOB_ATTEMPT_TIMEOUT_SECONDS",
        DEFAULT_GENERATION_JOB_ATTEMPT_TIMEOUT_SECONDS,
        minimum=1,
        maximum=86_400,
    )
    generation_job_concurrency = _bounded_positive_integer_setting(
        "GENERATION_JOB_CONCURRENCY",
        DEFAULT_GENERATION_JOB_CONCURRENCY,
        minimum=1,
        maximum=MAX_GENERATION_JOB_CONCURRENCY,
    )
    generation_job_max_active_per_user = _bounded_positive_integer_setting(
        "GENERATION_JOB_MAX_ACTIVE_PER_USER",
        DEFAULT_GENERATION_JOB_MAX_ACTIVE_PER_USER,
        minimum=1,
        maximum=MAX_GENERATION_JOB_MAX_ACTIVE_PER_USER,
    )
    if mode == MODE_HOSTED:
        # One worker process runs both pools. Every slot costs a job connection
        # plus its heartbeat connection, and each pool has its own coordinator.
        peak_worker_connections = (
            2 * processing_job_concurrency + 2 * generation_job_concurrency + 2
        )
        if peak_worker_connections > database_pool_size + database_max_overflow:
            raise ValueError(
                "PROCESSING_JOB_CONCURRENCY and GENERATION_JOB_CONCURRENCY "
                f"require {peak_worker_connections} database connections but "
                "DATABASE_POOL_SIZE plus DATABASE_MAX_OVERFLOW allow only "
                f"{database_pool_size + database_max_overflow}."
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
    for name, default in (
        ("STUDY_GUIDE_MATERIAL_MAX_CHARS", DEFAULT_CITED_MATERIAL_MAX_CHARACTERS),
        ("QUIZ_MATERIAL_MAX_CHARS", DEFAULT_CITED_MATERIAL_MAX_CHARACTERS),
        ("FLASHCARD_MATERIAL_MAX_CHARS", DEFAULT_MATERIAL_MAX_CHARACTERS),
        ("AI_TUTOR_MATERIAL_MAX_CHARS", DEFAULT_CITED_MATERIAL_MAX_CHARACTERS),
        ("COURSE_QA_MATERIAL_MAX_CHARS", DEFAULT_CITED_MATERIAL_MAX_CHARACTERS),
        (
            "EXAM_ANALYSIS_MATERIAL_MAX_CHARS",
            DEFAULT_CITED_MATERIAL_MAX_CHARACTERS,
        ),
        ("EXAM_PAST_PAPER_MAX_CHARS", DEFAULT_CITED_MATERIAL_MAX_CHARACTERS),
        ("EXAM_TOPIC_GUIDE_MATERIAL_MAX_CHARS", DEFAULT_CITED_MATERIAL_MAX_CHARACTERS),
        (
            "EXAM_TOPIC_SUMMARY_MATERIAL_MAX_CHARS",
            DEFAULT_CITED_MATERIAL_MAX_CHARACTERS,
        ),
        ("EXAM_TOPIC_QUIZ_MATERIAL_MAX_CHARS", DEFAULT_CITED_MATERIAL_MAX_CHARACTERS),
        (
            "EXAM_SIMILAR_QUESTIONS_MATERIAL_MAX_CHARS",
            DEFAULT_CITED_MATERIAL_MAX_CHARACTERS,
        ),
        ("EXAM_MOCK_EXAM_MATERIAL_MAX_CHARS", DEFAULT_CITED_MATERIAL_MAX_CHARACTERS),
        (
            "EXAM_REVIEW_SHEET_MATERIAL_MAX_CHARS",
            DEFAULT_CITED_MATERIAL_MAX_CHARACTERS,
        ),
    ):
        budget = _positive_integer_setting(name, default)
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
    ai_generation_overall_timeout_seconds = _bounded_positive_integer_setting(
        "AI_GENERATION_OVERALL_TIMEOUT_SECONDS",
        DEFAULT_AI_GENERATION_OVERALL_TIMEOUT_SECONDS,
        minimum=1,
        maximum=300,
    )
    # Grading runs inside the transaction that writes the attempt, and a
    # hosted database closes a transaction left idle for sixty seconds.
    # Bounded below that, so a slow grader costs a student their marks
    # rather than the answers they wrote.
    ai_grading_overall_timeout_seconds = _bounded_positive_integer_setting(
        "AI_GRADING_OVERALL_TIMEOUT_SECONDS",
        DEFAULT_AI_GRADING_OVERALL_TIMEOUT_SECONDS,
        minimum=1,
        maximum=55,
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

    raw_image_provider = os.getenv("IMAGE_PROVIDER")
    if raw_image_provider is not None and raw_image_provider.strip():
        image_provider = raw_image_provider.strip().lower()
    else:
        if mode == MODE_HOSTED:
            if ai_provider == AI_PROVIDER_GEMINI:
                gemini_entries = ai_model_catalog.get(AI_PROVIDER_GEMINI, [])
                if any(bool(entry.get("vision")) for entry in gemini_entries):
                    image_provider = IMAGE_PROVIDER_GEMINI
                else:
                    image_provider = IMAGE_PROVIDER_NONE
            else:
                image_provider = IMAGE_PROVIDER_NONE
        else:
            ollama_entries = ai_model_catalog.get(AI_PROVIDER_OLLAMA, [])
            configured_ollama_image_model = os.getenv(
                "OLLAMA_IMAGE_MODEL",
                DEFAULT_OLLAMA_IMAGE_MODEL,
            ).strip()
            has_advertised_vision = any(
                entry.get("model") == configured_ollama_image_model
                and bool(entry.get("vision"))
                for entry in ollama_entries
            )
            if has_advertised_vision:
                image_provider = IMAGE_PROVIDER_OLLAMA
            else:
                image_provider = IMAGE_PROVIDER_NONE
                logger.info(
                    "Visual analysis is disabled (IMAGE_PROVIDER='none'); visual content in uploaded documents will not be described or indexed for retrieval."
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

    if image_provider == IMAGE_PROVIDER_GEMINI:
        if not gemini_api_key:
            raise ValueError(
                "GEMINI_API_KEY is required when gemini is configured as IMAGE_PROVIDER."
            )
        gemini_entries = ai_model_catalog.get(AI_PROVIDER_GEMINI, [])
        for entry in gemini_entries:
            if (
                entry.get("model") == gemini_image_model
                and entry.get("vision") is False
            ):
                raise ValueError(
                    f"GEMINI_IMAGE_MODEL '{gemini_image_model}' is declared with vision=False in AI_MODEL_CATALOG."
                )
    elif image_provider == IMAGE_PROVIDER_OLLAMA:
        ollama_entries = ai_model_catalog.get(AI_PROVIDER_OLLAMA, [])
        for entry in ollama_entries:
            if (
                entry.get("model") == ollama_image_model
                and entry.get("vision") is False
            ):
                raise ValueError(
                    f"OLLAMA_IMAGE_MODEL '{ollama_image_model}' is declared with vision=False in AI_MODEL_CATALOG."
                )

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

    exam_quiz_default_question_count = _bounded_positive_integer_setting(
        "EXAM_QUIZ_DEFAULT_QUESTION_COUNT", 10, minimum=1, maximum=20
    )
    exam_mock_exam_question_count = _bounded_positive_integer_setting(
        "EXAM_MOCK_EXAM_QUESTION_COUNT", 20, minimum=1, maximum=20
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

    rate_limit_login_max_attempts = _positive_integer_setting(
        "RATE_LIMIT_LOGIN_MAX_ATTEMPTS", DEFAULT_RATE_LIMIT_LOGIN_MAX_ATTEMPTS
    )
    rate_limit_login_window_seconds = _positive_integer_setting(
        "RATE_LIMIT_LOGIN_WINDOW_SECONDS", DEFAULT_RATE_LIMIT_LOGIN_WINDOW_SECONDS
    )
    rate_limit_register_max_attempts = _positive_integer_setting(
        "RATE_LIMIT_REGISTER_MAX_ATTEMPTS", DEFAULT_RATE_LIMIT_REGISTER_MAX_ATTEMPTS
    )
    rate_limit_register_window_seconds = _positive_integer_setting(
        "RATE_LIMIT_REGISTER_WINDOW_SECONDS",
        DEFAULT_RATE_LIMIT_REGISTER_WINDOW_SECONDS,
    )
    rate_limit_generation_max_attempts = _positive_integer_setting(
        "RATE_LIMIT_GENERATION_MAX_ATTEMPTS",
        DEFAULT_RATE_LIMIT_GENERATION_MAX_ATTEMPTS,
    )
    rate_limit_generation_window_seconds = _positive_integer_setting(
        "RATE_LIMIT_GENERATION_WINDOW_SECONDS",
        DEFAULT_RATE_LIMIT_GENERATION_WINDOW_SECONDS,
    )
    rate_limit_lockout_base_seconds = _positive_integer_setting(
        "RATE_LIMIT_LOCKOUT_BASE_SECONDS", DEFAULT_RATE_LIMIT_LOCKOUT_BASE_SECONDS
    )
    rate_limit_lockout_max_seconds = _positive_integer_setting(
        "RATE_LIMIT_LOCKOUT_MAX_SECONDS", DEFAULT_RATE_LIMIT_LOCKOUT_MAX_SECONDS
    )
    if rate_limit_lockout_max_seconds < rate_limit_lockout_base_seconds:
        raise ValueError(
            "RATE_LIMIT_LOCKOUT_MAX_SECONDS must be at least "
            "RATE_LIMIT_LOCKOUT_BASE_SECONDS."
        )
    rate_limit_verification_max_attempts = _positive_integer_setting(
        "RATE_LIMIT_VERIFICATION_MAX_ATTEMPTS",
        DEFAULT_RATE_LIMIT_VERIFICATION_MAX_ATTEMPTS,
    )
    rate_limit_verification_window_seconds = _positive_integer_setting(
        "RATE_LIMIT_VERIFICATION_WINDOW_SECONDS",
        DEFAULT_RATE_LIMIT_VERIFICATION_WINDOW_SECONDS,
    )
    rate_limit_password_reset_max_attempts = _positive_integer_setting(
        "RATE_LIMIT_PASSWORD_RESET_MAX_ATTEMPTS",
        DEFAULT_RATE_LIMIT_PASSWORD_RESET_MAX_ATTEMPTS,
    )
    rate_limit_password_reset_window_seconds = _positive_integer_setting(
        "RATE_LIMIT_PASSWORD_RESET_WINDOW_SECONDS",
        DEFAULT_RATE_LIMIT_PASSWORD_RESET_WINDOW_SECONDS,
    )

    password_min_length = _bounded_positive_integer_setting(
        "PASSWORD_MIN_LENGTH",
        DEFAULT_PASSWORD_MIN_LENGTH,
        minimum=DEFAULT_PASSWORD_MIN_LENGTH,
        maximum=MAX_PASSWORD_MIN_LENGTH,
    )

    # Verification gates the introductory credits, so it defaults on exactly
    # where those credits are worth farming: a deployment whose inference the
    # operator pays for.
    email_verification_required = _boolean_setting(
        "EMAIL_VERIFICATION_REQUIRED",
        default=mode == MODE_HOSTED,
    )
    email_verification_token_ttl_hours = _bounded_positive_integer_setting(
        "EMAIL_VERIFICATION_TOKEN_TTL_HOURS",
        DEFAULT_EMAIL_VERIFICATION_TOKEN_TTL_HOURS,
        minimum=1,
        maximum=168,
    )
    access_token_expire_minutes = _bounded_positive_integer_setting(
        "ACCESS_TOKEN_EXPIRE_MINUTES",
        DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES,
        minimum=1,
        maximum=60 * 24 * 365,
    )
    password_reset_token_ttl_minutes = _bounded_positive_integer_setting(
        "PASSWORD_RESET_TOKEN_TTL_MINUTES",
        DEFAULT_PASSWORD_RESET_TOKEN_TTL_MINUTES,
        minimum=1,
        maximum=60 * 24 * 7,
    )
    app_public_base_url = os.getenv("APP_PUBLIC_BASE_URL", "").strip() or None
    if app_public_base_url is not None:
        app_public_base_url = _http_url_setting("APP_PUBLIC_BASE_URL", "")
    email_from_address = os.getenv("EMAIL_FROM_ADDRESS", "").strip() or None
    if email_from_address is not None:
        try:
            email_from_address = validate_email(
                email_from_address,
                check_deliverability=False,
            ).normalized
        except EmailNotValidError as exc:
            raise ValueError(
                "EMAIL_FROM_ADDRESS must be a valid email address."
            ) from exc
    smtp_host = os.getenv("SMTP_HOST", "").strip() or None
    smtp_port = _bounded_positive_integer_setting(
        "SMTP_PORT", DEFAULT_SMTP_PORT, minimum=1, maximum=65535
    )
    smtp_username = os.getenv("SMTP_USERNAME", "").strip() or None
    smtp_password = os.getenv("SMTP_PASSWORD") or None
    smtp_use_tls = _boolean_setting("SMTP_USE_TLS", default=True)
    smtp_timeout_seconds = _bounded_positive_integer_setting(
        "SMTP_TIMEOUT_SECONDS", DEFAULT_SMTP_TIMEOUT_SECONDS, minimum=1, maximum=120
    )
    if (smtp_username is None) != (smtp_password is None):
        raise ValueError("SMTP_USERNAME and SMTP_PASSWORD must be set together.")
    if email_verification_required:
        # A deployment that gates credits on a link it cannot send would hand
        # every new account a balance of zero and no way out of it.
        missing = [
            name
            for name, value in (
                ("APP_PUBLIC_BASE_URL", app_public_base_url),
                ("EMAIL_FROM_ADDRESS", email_from_address),
                ("SMTP_HOST", smtp_host),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "EMAIL_VERIFICATION_REQUIRED needs a way to deliver the "
                f"verification link; set {', '.join(missing)}."
            )

    security_headers_enabled = _boolean_setting(
        "SECURITY_HEADERS_ENABLED", default=True
    )
    # HSTS is a promise the browser remembers for a year, so it is only made by
    # default where TLS is known to terminate in front of the API. A self-hosted
    # operator serving over plain HTTP on a LAN would otherwise lock themselves
    # out of their own deployment; behind a TLS proxy they turn it on.
    hsts_enabled = _boolean_setting(
        "SECURITY_HSTS_ENABLED",
        default=mode == MODE_HOSTED,
    )
    hsts_max_age_seconds = _nonnegative_integer_setting(
        "SECURITY_HSTS_MAX_AGE_SECONDS", DEFAULT_HSTS_MAX_AGE_SECONDS
    )

    course_purge_interval_seconds = _nonnegative_float_setting(
        "COURSE_PURGE_INTERVAL_SECONDS",
        DEFAULT_COURSE_PURGE_INTERVAL_SECONDS,
    )
    embedding_backfill_interval_seconds = _nonnegative_float_setting(
        "EMBEDDING_BACKFILL_INTERVAL_SECONDS",
        DEFAULT_EMBEDDING_BACKFILL_INTERVAL_SECONDS,
    )
    embedding_backfill_batch_size = _positive_integer_setting(
        "EMBEDDING_BACKFILL_BATCH_SIZE",
        DEFAULT_EMBEDDING_BACKFILL_BATCH_SIZE,
    )
    embedding_backfill_prune_orphans = _boolean_setting(
        "EMBEDDING_BACKFILL_PRUNE_ORPHANS",
        default=DEFAULT_EMBEDDING_BACKFILL_PRUNE_ORPHANS,
    )
    ai_usage_retention_days = _positive_integer_setting(
        "AI_USAGE_RETENTION_DAYS",
        DEFAULT_AI_USAGE_RETENTION_DAYS,
    )
    ai_usage_cleanup_batch_size = _positive_integer_setting(
        "AI_USAGE_CLEANUP_BATCH_SIZE",
        DEFAULT_AI_USAGE_CLEANUP_BATCH_SIZE,
    )

    if mode == MODE_SELF_HOSTED:
        raw_ads = os.getenv("ENABLE_HOSTED_ADS")
        if raw_ads is not None and _boolean_setting("ENABLE_HOSTED_ADS", default=False):
            raise ValueError(
                "Self-hosted deployment mode does not permit ENABLE_HOSTED_ADS=true. "
                "Advertising is strictly isolated to hosted deployments."
            )
        enable_hosted_ads = False
        hosted_ads_provider = None
        hosted_ads_publisher_id = None
    else:
        enable_hosted_ads = _boolean_setting(
            "ENABLE_HOSTED_ADS",
            default=DEFAULT_ENABLE_HOSTED_ADS,
        )
        if enable_hosted_ads:
            hosted_ads_provider = (
                os.getenv("HOSTED_ADS_PROVIDER", DEFAULT_HOSTED_ADS_PROVIDER).strip()
                or DEFAULT_HOSTED_ADS_PROVIDER
            )
            hosted_ads_publisher_id = (
                os.getenv(
                    "HOSTED_ADS_PUBLISHER_ID", DEFAULT_HOSTED_ADS_PUBLISHER_ID
                ).strip()
                or DEFAULT_HOSTED_ADS_PUBLISHER_ID
            )
        else:
            hosted_ads_provider = None
            hosted_ads_publisher_id = None

    return Settings(
        app_env=app_env,
        app_debug=app_debug,
        cors_allowed_origins=cors_allowed_origins,
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
        ai_model_catalog=ai_model_catalog,
        ai_pricing_version=ai_pricing_version,
        ai_model_cost_rates=ai_model_cost_rates,
        gemini_api_key=gemini_api_key,
        openai_api_key=openai_api_key,
        anthropic_api_key=anthropic_api_key,
        ollama_base_url=ollama_base_url,
        ollama_model=ollama_model,
        ollama_temperature=ollama_temperature,
        ollama_top_p=ollama_top_p,
        ollama_num_ctx=ollama_num_ctx,
        ollama_num_predict=ollama_num_predict,
        ollama_repeat_penalty=ollama_repeat_penalty,
        ai_fallback_providers=ai_fallback_providers,
        ai_generation_timeout_seconds=ai_generation_timeout_seconds,
        ai_generation_max_attempts=ai_generation_max_attempts,
        ai_generation_backoff_base_seconds=ai_generation_backoff_base_seconds,
        ai_generation_backoff_max_seconds=ai_generation_backoff_max_seconds,
        ai_generation_max_concurrency=ai_generation_max_concurrency,
        ai_generation_overall_timeout_seconds=ai_generation_overall_timeout_seconds,
        ai_grading_overall_timeout_seconds=ai_grading_overall_timeout_seconds,
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
        processing_job_concurrency=processing_job_concurrency,
        generation_job_lease_seconds=generation_job_lease_seconds,
        generation_job_max_attempts=generation_job_max_attempts,
        generation_job_poll_seconds=generation_job_poll_seconds,
        generation_job_attempt_timeout_seconds=generation_job_attempt_timeout_seconds,
        generation_job_concurrency=generation_job_concurrency,
        generation_job_max_active_per_user=generation_job_max_active_per_user,
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
        exam_analysis_material_max_chars=material_budgets[
            "EXAM_ANALYSIS_MATERIAL_MAX_CHARS"
        ],
        exam_past_paper_max_chars=material_budgets["EXAM_PAST_PAPER_MAX_CHARS"],
        exam_topic_guide_material_max_chars=material_budgets[
            "EXAM_TOPIC_GUIDE_MATERIAL_MAX_CHARS"
        ],
        exam_topic_summary_material_max_chars=material_budgets[
            "EXAM_TOPIC_SUMMARY_MATERIAL_MAX_CHARS"
        ],
        exam_topic_quiz_material_max_chars=material_budgets[
            "EXAM_TOPIC_QUIZ_MATERIAL_MAX_CHARS"
        ],
        exam_similar_questions_material_max_chars=material_budgets[
            "EXAM_SIMILAR_QUESTIONS_MATERIAL_MAX_CHARS"
        ],
        exam_mock_exam_material_max_chars=material_budgets[
            "EXAM_MOCK_EXAM_MATERIAL_MAX_CHARS"
        ],
        exam_review_sheet_material_max_chars=material_budgets[
            "EXAM_REVIEW_SHEET_MATERIAL_MAX_CHARS"
        ],
        exam_mock_exam_question_count=exam_mock_exam_question_count,
        exam_quiz_default_question_count=exam_quiz_default_question_count,
        credit_metering_enabled=credit_metering_enabled,
        credit_initial_grant=credit_initial_grant,
        credit_periodic_grant=credit_periodic_grant,
        credit_max_balance=credit_max_balance,
        rate_limit_login_max_attempts=rate_limit_login_max_attempts,
        rate_limit_login_window_seconds=rate_limit_login_window_seconds,
        rate_limit_register_max_attempts=rate_limit_register_max_attempts,
        rate_limit_register_window_seconds=rate_limit_register_window_seconds,
        rate_limit_generation_max_attempts=rate_limit_generation_max_attempts,
        rate_limit_generation_window_seconds=rate_limit_generation_window_seconds,
        rate_limit_lockout_base_seconds=rate_limit_lockout_base_seconds,
        rate_limit_lockout_max_seconds=rate_limit_lockout_max_seconds,
        rate_limit_verification_max_attempts=rate_limit_verification_max_attempts,
        rate_limit_verification_window_seconds=rate_limit_verification_window_seconds,
        rate_limit_password_reset_max_attempts=rate_limit_password_reset_max_attempts,
        rate_limit_password_reset_window_seconds=rate_limit_password_reset_window_seconds,
        password_min_length=password_min_length,
        email_verification_required=email_verification_required,
        email_verification_token_ttl_hours=email_verification_token_ttl_hours,
        access_token_expire_minutes=access_token_expire_minutes,
        password_reset_token_ttl_minutes=password_reset_token_ttl_minutes,
        app_public_base_url=app_public_base_url,
        email_from_address=email_from_address,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_username=smtp_username,
        smtp_password=smtp_password,
        smtp_use_tls=smtp_use_tls,
        smtp_timeout_seconds=smtp_timeout_seconds,
        security_headers_enabled=security_headers_enabled,
        hsts_enabled=hsts_enabled,
        hsts_max_age_seconds=hsts_max_age_seconds,
        course_purge_interval_seconds=course_purge_interval_seconds,
        embedding_backfill_interval_seconds=embedding_backfill_interval_seconds,
        embedding_backfill_batch_size=embedding_backfill_batch_size,
        embedding_backfill_prune_orphans=embedding_backfill_prune_orphans,
        ai_usage_retention_days=ai_usage_retention_days,
        ai_usage_cleanup_batch_size=ai_usage_cleanup_batch_size,
        enable_hosted_ads=enable_hosted_ads,
        hosted_ads_provider=hosted_ads_provider,
        hosted_ads_publisher_id=hosted_ads_publisher_id,
    )


def _cors_allowed_origins_setting() -> tuple[str, ...]:
    raw_value = os.getenv("CORS_ALLOWED_ORIGINS", "")
    if any(
        ord(character) < 32 or 127 <= ord(character) <= 159 for character in raw_value
    ):
        raise ValueError("CORS_ALLOWED_ORIGINS must not contain control characters.")
    if not raw_value.strip():
        return ()

    origins: list[str] = []
    seen: set[str] = set()
    for raw_origin in raw_value.split(","):
        origin = raw_origin.strip()
        if not origin:
            raise ValueError(
                "CORS_ALLOWED_ORIGINS must contain comma-separated origins."
            )
        if origin in {"*", "null"}:
            raise ValueError(
                "CORS_ALLOWED_ORIGINS does not allow wildcard or null origins."
            )
        if any(character.isspace() for character in origin):
            raise ValueError(
                "CORS_ALLOWED_ORIGINS origins must not contain whitespace."
            )

        try:
            parts = urlsplit(origin)
            port = parts.port
        except ValueError as exc:
            raise ValueError(
                "CORS_ALLOWED_ORIGINS must contain valid http:// or https:// origins."
            ) from exc

        hostname = parts.hostname
        if (
            parts.scheme not in {"http", "https"}
            or hostname is None
            or parts.username is not None
            or parts.password is not None
            or parts.path
            or "?" in origin
            or "#" in origin
        ):
            raise ValueError(
                "CORS_ALLOWED_ORIGINS must contain exact http:// or https:// origins "
                "without userinfo, paths, queries, or fragments."
            )
        if "*" in hostname:
            raise ValueError("CORS_ALLOWED_ORIGINS does not allow wildcard hosts.")

        if ":" in hostname:
            if "%" in hostname:
                raise ValueError(
                    "CORS_ALLOWED_ORIGINS must contain valid serialized hosts."
                )
            try:
                address = ip_address(hostname)
            except ValueError as exc:
                raise ValueError(
                    "CORS_ALLOWED_ORIGINS must contain valid serialized hosts."
                ) from exc
            if not isinstance(address, IPv6Address) or hostname != address.compressed:
                raise ValueError(
                    "CORS_ALLOWED_ORIGINS must contain exact serialized origins."
                )
            serialized_host = f"[{address.compressed}]"
        else:
            try:
                ascii_hostname = hostname.encode("idna").decode("ascii")
            except UnicodeError as exc:
                raise ValueError(
                    "CORS_ALLOWED_ORIGINS must contain valid serialized hosts."
                ) from exc
            labels = (
                ascii_hostname[:-1] if ascii_hostname.endswith(".") else ascii_hostname
            )
            hostname_labels = labels.split(".")
            if any(label.startswith("0x") for label in hostname_labels):
                raise ValueError(
                    "CORS_ALLOWED_ORIGINS must contain exact serialized origins."
                )
            if re.fullmatch(r"[0-9.]+", hostname):
                try:
                    address = ip_address(hostname)
                except ValueError as exc:
                    raise ValueError(
                        "CORS_ALLOWED_ORIGINS must contain valid serialized hosts."
                    ) from exc
                if not isinstance(address, IPv4Address) or hostname != str(address):
                    raise ValueError(
                        "CORS_ALLOWED_ORIGINS must contain exact serialized origins."
                    )
            elif (
                ascii_hostname != hostname
                or not labels
                or len(ascii_hostname) > 253
                or any(
                    not label
                    or len(label) > 63
                    or re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label) is None
                    for label in hostname_labels
                )
            ):
                raise ValueError(
                    "CORS_ALLOWED_ORIGINS must contain valid serialized hosts."
                )
            if any(label.startswith("xn--") for label in hostname_labels):
                try:
                    normalized_domain = idna.encode(
                        hostname,
                        uts46=True,
                        std3_rules=True,
                        transitional=False,
                    ).decode("ascii")
                except idna.IDNAError as exc:
                    raise ValueError(
                        "CORS_ALLOWED_ORIGINS must contain valid serialized hosts."
                    ) from exc
                if normalized_domain != hostname:
                    raise ValueError(
                        "CORS_ALLOWED_ORIGINS must contain exact serialized origins."
                    )
            serialized_host = ascii_hostname

        if (parts.scheme, port) in {("http", 80), ("https", 443)}:
            raise ValueError(
                "CORS_ALLOWED_ORIGINS must omit default ports from serialized origins."
            )

        serialized_origin = f"{parts.scheme}://{serialized_host}"
        if port is not None:
            serialized_origin += f":{port}"
        if origin != serialized_origin:
            raise ValueError(
                "CORS_ALLOWED_ORIGINS must contain exact serialized origins."
            )
        if origin in seen:
            raise ValueError("CORS_ALLOWED_ORIGINS must not contain duplicates.")
        seen.add(origin)
        origins.append(origin)

    return tuple(origins)


def _ai_model_catalog_setting(
    ollama_model: str,
    ollama_num_ctx: int,
) -> dict[str, list[dict[str, object]]]:
    raw_value = os.getenv("AI_MODEL_CATALOG", "").strip()

    if not raw_value:
        return {
            AI_PROVIDER_OLLAMA: [
                {
                    "model": ollama_model,
                    "json_mode": True,
                    "context_window": ollama_num_ctx,
                    "vision": False,
                }
            ],
            AI_PROVIDER_GEMINI: [
                {
                    "model": DEFAULT_GEMINI_MODEL,
                    "json_mode": True,
                    "context_window": 1_048_576,
                    "vision": True,
                }
            ],
            AI_PROVIDER_OPENAI: [
                {
                    "model": DEFAULT_OPENAI_MODEL,
                    "json_mode": True,
                    "context_window": 1_048_576,
                    "vision": True,
                }
            ],
            AI_PROVIDER_CLAUDE: [
                {
                    "model": DEFAULT_CLAUDE_MODEL,
                    "json_mode": True,
                    "context_window": 1_048_576,
                    "vision": True,
                }
            ],
        }

    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ValueError("AI_MODEL_CATALOG must be valid JSON.") from exc

    if not isinstance(parsed, dict) or not parsed:
        raise ValueError(
            "AI_MODEL_CATALOG must be a non-empty JSON object keyed by provider."
        )

    for provider, models in parsed.items():
        if provider not in IMPLEMENTED_AI_PROVIDERS:
            raise ValueError(
                "AI_MODEL_CATALOG provider must be one of the implemented providers: "
                f"{', '.join(IMPLEMENTED_AI_PROVIDERS)}; got '{provider}'."
            )

        if not isinstance(models, list) or not models:
            raise ValueError(
                f"AI_MODEL_CATALOG provider '{provider}' must map to a non-empty "
                "list of models."
            )

        required_fields = {"model", "json_mode", "context_window", "vision"}
        seen_models: set[str] = set()

        for entry in models:
            if not isinstance(entry, dict):
                raise ValueError(
                    f"AI_MODEL_CATALOG provider '{provider}' entries must be "
                    "JSON objects."
                )

            missing_fields = required_fields - entry.keys()
            if missing_fields:
                missing = ", ".join(sorted(missing_fields))
                raise ValueError(
                    f"AI_MODEL_CATALOG provider '{provider}' model entry is "
                    f"missing required fields: {missing}."
                )

            model = entry["model"]
            json_mode = entry["json_mode"]
            context_window = entry["context_window"]
            vision = entry["vision"]

            if not isinstance(model, str) or not model.strip():
                raise ValueError(
                    f"AI_MODEL_CATALOG provider '{provider}' model must be a "
                    "non-empty string."
                )

            if len(model) > 128:
                raise ValueError(
                    f"AI_MODEL_CATALOG provider '{provider}' model must be at "
                    "most 128 characters."
                )

            if not isinstance(json_mode, bool):
                raise ValueError(
                    f"AI_MODEL_CATALOG provider '{provider}' json_mode must be "
                    "a boolean."
                )

            if (
                not isinstance(context_window, int)
                or isinstance(context_window, bool)
                or context_window <= 0
            ):
                raise ValueError(
                    f"AI_MODEL_CATALOG provider '{provider}' context_window must "
                    "be a positive integer."
                )

            if not isinstance(vision, bool):
                raise ValueError(
                    f"AI_MODEL_CATALOG provider '{provider}' vision must be a boolean."
                )

            if model in seen_models:
                raise ValueError(
                    f"AI_MODEL_CATALOG provider '{provider}' contains duplicate "
                    f"model '{model}'."
                )

            seen_models.add(model)

    return parsed


def _ai_model_cost_rates_setting() -> tuple[str | None, dict[str, dict[str, float]]]:
    raw_value = os.getenv("AI_MODEL_COST_RATES", "").strip()
    if not raw_value:
        return None, {}

    try:
        configured = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ValueError("AI_MODEL_COST_RATES must be valid JSON.") from exc
    if not isinstance(configured, dict) or set(configured) != {"version", "models"}:
        raise ValueError(
            "AI_MODEL_COST_RATES must contain exactly 'version' and 'models'."
        )

    version = configured["version"]
    models = configured["models"]
    if not isinstance(version, str) or not version.strip() or len(version) > 100:
        raise ValueError(
            "AI_MODEL_COST_RATES version must be a non-empty string of at most 100 characters."
        )
    if not isinstance(models, dict):
        raise ValueError("AI_MODEL_COST_RATES models must be a JSON object.")

    normalized: dict[str, dict[str, float]] = {}
    expected_fields = {
        "prompt_usd_per_million_tokens",
        "completion_usd_per_million_tokens",
    }
    for identity, rates in models.items():
        if not isinstance(identity, str) or not identity.strip() or len(identity) > 200:
            raise ValueError(
                "AI_MODEL_COST_RATES model keys must be non-empty provider:model strings."
            )
        provider, separator, model = identity.strip().partition(":")
        model = model.strip()
        if not separator or provider not in IMPLEMENTED_AI_PROVIDERS or not model:
            raise ValueError(
                "AI_MODEL_COST_RATES model keys must use an implemented provider:model identity."
            )
        if len(model) > 128:
            raise ValueError(
                "AI_MODEL_COST_RATES model names must be at most 128 characters."
            )
        normalized_identity = f"{provider}:{model}"
        if normalized_identity in normalized:
            raise ValueError(
                f"AI_MODEL_COST_RATES contains duplicate model '{normalized_identity}'."
            )
        if not isinstance(rates, dict) or set(rates) != expected_fields:
            raise ValueError(
                f"AI_MODEL_COST_RATES entry '{identity}' must contain exactly "
                "prompt_usd_per_million_tokens and completion_usd_per_million_tokens."
            )

        normalized_rates: dict[str, float] = {}
        for field in expected_fields:
            value = rates[field]
            if isinstance(value, bool):
                raise ValueError(
                    f"AI_MODEL_COST_RATES {identity}.{field} must be a number."
                )
            try:
                numeric = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"AI_MODEL_COST_RATES {identity}.{field} must be a number."
                ) from exc
            if (
                not math.isfinite(numeric)
                or numeric < 0
                or numeric > MAX_AI_MODEL_COST_RATE_USD_PER_MILLION
            ):
                raise ValueError(
                    f"AI_MODEL_COST_RATES {identity}.{field} must be between 0 and "
                    f"{MAX_AI_MODEL_COST_RATE_USD_PER_MILLION}."
                )
            normalized_rates[field] = numeric
        normalized[normalized_identity] = normalized_rates

    return version.strip(), normalized


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
