import os
import subprocess
import sys
from pathlib import Path

import pytest

from backend.app.config import (
    APP_ENV_DEVELOPMENT,
    APP_ENV_PRODUCTION,
    APP_ENV_STAGING,
    DEFAULT_MAX_CONCURRENT_DOCUMENT_VALIDATIONS,
    DEFAULT_MAX_COURSE_STORAGE_BYTES,
    DEFAULT_MAX_DOCUMENT_CHUNKS,
    DEFAULT_MAX_DOCUMENTS_PER_COURSE,
    DEFAULT_MAX_EXTRACTED_CHARACTERS,
    DEFAULT_MAX_PDF_CONTENT_STREAM_BYTES,
    DEFAULT_MAX_PDF_DRAWING_OPERATIONS,
    DEFAULT_MAX_PDF_PAGE_PIXELS,
    DEFAULT_MAX_PDF_PAGES,
    DEFAULT_MAX_PDF_TOTAL_PIXELS,
    DEFAULT_MAX_REQUEST_SIZE_BYTES,
    DEFAULT_MAX_UPLOAD_SIZE_BYTES,
    DEFAULT_DOCUMENT_CHUNK_OVERLAP_CHARACTERS,
    DEFAULT_DOCUMENT_CHUNK_SIZE_CHARACTERS,
    DEFAULT_OCR_DPI,
    DEFAULT_OCR_LANGUAGE,
    DEFAULT_OCR_MIN_TEXT_CHARACTERS,
    DEFAULT_PROCESSING_JOB_ATTEMPT_TIMEOUT_SECONDS,
    DEFAULT_PROCESSING_JOB_LEASE_SECONDS,
    DEFAULT_PROCESSING_JOB_MAX_ATTEMPTS,
    DEFAULT_PROCESSING_JOB_POLL_SECONDS,
    DEFAULT_UPLOAD_REQUEST_TIMEOUT_SECONDS,
    MODE_HOSTED,
    MODE_SELF_HOSTED,
    load_settings,
)
from backend.app.database_config import load_database_url

PROJECT_ROOT = Path(__file__).resolve().parents[1]

CONFIGURATION_KEYS = (
    "APP_ENV",
    "APP_DEBUG",
    "DEPLOYMENT_MODE",
    "AI_PROVIDER",
    "DATABASE_URL",
    "STORAGE_BACKEND",
    "STORAGE_NAMESPACE",
    "UPLOAD_DIRECTORY",
    "CHROMA_PERSIST_DIRECTORY",
    "JWT_SECRET_KEY",
    "BOOTSTRAP_ADMIN_EMAIL",
    "BOOTSTRAP_ADMIN_TOKEN",
    "GEMINI_API_KEY",
    "MAX_UPLOAD_SIZE_BYTES",
    "MAX_REQUEST_SIZE_BYTES",
    "MAX_CONCURRENT_DOCUMENT_VALIDATIONS",
    "UPLOAD_REQUEST_TIMEOUT_SECONDS",
    "MAX_DOCUMENTS_PER_COURSE",
    "MAX_COURSE_STORAGE_BYTES",
    "MAX_PDF_PAGES",
    "MAX_PDF_PAGE_PIXELS",
    "MAX_PDF_TOTAL_PIXELS",
    "MAX_PDF_CONTENT_STREAM_BYTES",
    "MAX_PDF_DRAWING_OPERATIONS",
    "PROCESSING_JOB_LEASE_SECONDS",
    "PROCESSING_JOB_MAX_ATTEMPTS",
    "PROCESSING_JOB_POLL_SECONDS",
    "PROCESSING_JOB_ATTEMPT_TIMEOUT_SECONDS",
    "MAX_EXTRACTED_CHARACTERS",
    "MAX_DOCUMENT_CHUNKS",
    "OCR_LANGUAGE",
    "OCR_DPI",
    "OCR_MIN_TEXT_CHARACTERS",
    "DOCUMENT_CHUNK_SIZE_CHARACTERS",
    "DOCUMENT_CHUNK_OVERLAP_CHARACTERS",
)


@pytest.fixture(autouse=True)
def clear_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in CONFIGURATION_KEYS:
        monkeypatch.delenv(key, raising=False)


def _configure_production(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "lumina.sqlite3"
    monkeypatch.setenv("APP_ENV", APP_ENV_PRODUCTION)
    monkeypatch.setenv("APP_DEBUG", "false")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    monkeypatch.setenv("UPLOAD_DIRECTORY", str(tmp_path / "uploads"))
    monkeypatch.setenv("CHROMA_PERSIST_DIRECTORY", str(tmp_path / "chroma"))
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 32)
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_TOKEN", "y" * 32)


def test_self_hosted_defaults_are_safe_and_runnable() -> None:
    loaded = load_settings()

    assert loaded.app_env == APP_ENV_DEVELOPMENT
    assert loaded.app_debug is True
    assert loaded.deployment_mode == MODE_SELF_HOSTED
    assert loaded.database_url == "sqlite:///./data/lumina.db"
    assert loaded.storage_backend == "local"
    assert loaded.storage_namespace == "self-hosted"
    assert loaded.bootstrap_admin_email is None
    assert len(loaded.jwt_secret_key) >= 32
    assert loaded.max_upload_size_bytes == DEFAULT_MAX_UPLOAD_SIZE_BYTES
    assert loaded.max_request_size_bytes == DEFAULT_MAX_REQUEST_SIZE_BYTES
    assert (
        loaded.max_concurrent_document_validations
        == DEFAULT_MAX_CONCURRENT_DOCUMENT_VALIDATIONS
    )
    assert (
        loaded.upload_request_timeout_seconds == DEFAULT_UPLOAD_REQUEST_TIMEOUT_SECONDS
    )
    assert loaded.max_documents_per_course == DEFAULT_MAX_DOCUMENTS_PER_COURSE
    assert loaded.max_course_storage_bytes == DEFAULT_MAX_COURSE_STORAGE_BYTES
    assert loaded.max_pdf_pages == DEFAULT_MAX_PDF_PAGES
    assert loaded.max_pdf_page_pixels == DEFAULT_MAX_PDF_PAGE_PIXELS
    assert loaded.max_pdf_total_pixels == DEFAULT_MAX_PDF_TOTAL_PIXELS
    assert loaded.max_pdf_content_stream_bytes == DEFAULT_MAX_PDF_CONTENT_STREAM_BYTES
    assert loaded.max_pdf_drawing_operations == DEFAULT_MAX_PDF_DRAWING_OPERATIONS
    assert loaded.processing_job_lease_seconds == DEFAULT_PROCESSING_JOB_LEASE_SECONDS
    assert loaded.processing_job_max_attempts == DEFAULT_PROCESSING_JOB_MAX_ATTEMPTS
    assert loaded.processing_job_poll_seconds == DEFAULT_PROCESSING_JOB_POLL_SECONDS
    assert (
        loaded.processing_job_attempt_timeout_seconds
        == DEFAULT_PROCESSING_JOB_ATTEMPT_TIMEOUT_SECONDS
    )
    assert loaded.max_extracted_characters == DEFAULT_MAX_EXTRACTED_CHARACTERS
    assert loaded.max_document_chunks == DEFAULT_MAX_DOCUMENT_CHUNKS
    assert loaded.ocr_language == DEFAULT_OCR_LANGUAGE
    assert loaded.ocr_dpi == DEFAULT_OCR_DPI
    assert loaded.ocr_min_text_characters == DEFAULT_OCR_MIN_TEXT_CHARACTERS
    assert (
        loaded.document_chunk_size_characters == DEFAULT_DOCUMENT_CHUNK_SIZE_CHARACTERS
    )
    assert (
        loaded.document_chunk_overlap_characters
        == DEFAULT_DOCUMENT_CHUNK_OVERLAP_CHARACTERS
    )


def test_application_does_not_load_discovered_dotenv_file(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "APP_ENV=production\nDEPLOYMENT_MODE=hosted\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.pop("APP_ENV", None)
    environment.pop("DEPLOYMENT_MODE", None)
    environment["PYTHONPATH"] = str(PROJECT_ROOT)

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from backend.app.database_config import load_app_environment; "
            "print(load_app_environment())",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "development"


def test_staging_defaults_to_debug_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", APP_ENV_STAGING)

    loaded = load_settings()

    assert loaded.app_env == APP_ENV_STAGING
    assert loaded.app_debug is False


def test_application_environment_is_validated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "preview")

    with pytest.raises(ValueError, match="APP_ENV"):
        load_settings()


@pytest.mark.parametrize("value", ["", "debug", "2"])
def test_application_debug_must_be_boolean(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("APP_DEBUG", value)

    with pytest.raises(ValueError, match="APP_DEBUG"):
        load_settings()


def test_valid_self_hosted_production_configuration_loads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_production(monkeypatch, tmp_path)

    loaded = load_settings()

    assert loaded.app_env == APP_ENV_PRODUCTION
    assert loaded.app_debug is False
    assert loaded.database_url.startswith("sqlite:///")


def test_production_rejects_debug_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_production(monkeypatch, tmp_path)
    monkeypatch.setenv("APP_DEBUG", "true")

    with pytest.raises(ValueError, match="APP_DEBUG"):
        load_settings()


@pytest.mark.parametrize(
    ("missing_name", "message"),
    [
        ("JWT_SECRET_KEY", "JWT_SECRET_KEY"),
        ("DATABASE_URL", "DATABASE_URL"),
        ("BOOTSTRAP_ADMIN_EMAIL", "BOOTSTRAP_ADMIN_EMAIL"),
        ("BOOTSTRAP_ADMIN_TOKEN", "BOOTSTRAP_ADMIN_TOKEN"),
    ],
)
def test_production_requires_explicit_security_and_database_configuration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    missing_name: str,
    message: str,
) -> None:
    _configure_production(monkeypatch, tmp_path)
    monkeypatch.delenv(missing_name)

    with pytest.raises(ValueError, match=message):
        load_settings()


@pytest.mark.parametrize(
    "database_url",
    ["sqlite:///relative/lumina.sqlite3", "sqlite:///~/lumina.sqlite3"],
)
def test_production_sqlite_database_path_must_be_absolute(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    database_url: str,
) -> None:
    _configure_production(monkeypatch, tmp_path)
    monkeypatch.setenv("DATABASE_URL", database_url)

    with pytest.raises(ValueError, match="absolute path"):
        load_settings()


def test_production_sqlite_parent_directory_must_exist(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_production(monkeypatch, tmp_path)
    database_path = tmp_path / "missing" / "lumina.sqlite3"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")

    with pytest.raises(ValueError, match="parent directory"):
        load_settings()


def test_database_only_loader_enforces_production_database_safety(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", APP_ENV_PRODUCTION)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///relative/lumina.sqlite3")

    with pytest.raises(ValueError, match="absolute path"):
        load_database_url()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("UPLOAD_DIRECTORY", "relative/path"),
        ("UPLOAD_DIRECTORY", "~/uploads"),
        ("CHROMA_PERSIST_DIRECTORY", "relative/path"),
        ("CHROMA_PERSIST_DIRECTORY", "~/chroma"),
    ],
)
def test_production_storage_paths_must_be_absolute(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    name: str,
    value: str,
) -> None:
    _configure_production(monkeypatch, tmp_path)
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=name):
        load_settings()


def test_hosted_production_is_not_supported(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_production(monkeypatch, tmp_path)
    monkeypatch.setenv("DEPLOYMENT_MODE", MODE_HOSTED)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://lumina:password@localhost:5432/lumina",
    )
    monkeypatch.setenv("STORAGE_NAMESPACE", "hosted-shared-volume")

    with pytest.raises(ValueError, match="Hosted production is not supported"):
        load_settings()


def test_self_hosted_production_rejects_unqualified_postgresql(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_production(monkeypatch, tmp_path)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://lumina:password@localhost:5432/lumina",
    )

    with pytest.raises(ValueError, match="PostgreSQL is not supported"):
        load_settings()


def test_upload_limit_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_UPLOAD_SIZE_BYTES", "1024")
    monkeypatch.setenv("MAX_REQUEST_SIZE_BYTES", "512")
    monkeypatch.setenv("MAX_CONCURRENT_DOCUMENT_VALIDATIONS", "3")
    monkeypatch.setenv("UPLOAD_REQUEST_TIMEOUT_SECONDS", "45")
    monkeypatch.setenv("MAX_DOCUMENTS_PER_COURSE", "4")
    monkeypatch.setenv("MAX_COURSE_STORAGE_BYTES", "5000")
    monkeypatch.setenv("MAX_PDF_PAGES", "12")
    monkeypatch.setenv("MAX_PDF_PAGE_PIXELS", "1000")
    monkeypatch.setenv("MAX_PDF_TOTAL_PIXELS", "2000")
    monkeypatch.setenv("MAX_PDF_CONTENT_STREAM_BYTES", "3000")
    monkeypatch.setenv("MAX_PDF_DRAWING_OPERATIONS", "4000")
    monkeypatch.setenv("PROCESSING_JOB_LEASE_SECONDS", "90")
    monkeypatch.setenv("PROCESSING_JOB_MAX_ATTEMPTS", "5")
    monkeypatch.setenv("PROCESSING_JOB_POLL_SECONDS", "0.25")
    monkeypatch.setenv("PROCESSING_JOB_ATTEMPT_TIMEOUT_SECONDS", "120")
    monkeypatch.setenv("MAX_EXTRACTED_CHARACTERS", "6000")
    monkeypatch.setenv("MAX_DOCUMENT_CHUNKS", "7")
    monkeypatch.setenv("OCR_LANGUAGE", "eng+deu")
    monkeypatch.setenv("OCR_DPI", "400")
    monkeypatch.setenv("OCR_MIN_TEXT_CHARACTERS", "0")
    monkeypatch.setenv("DOCUMENT_CHUNK_SIZE_CHARACTERS", "500")
    monkeypatch.setenv("DOCUMENT_CHUNK_OVERLAP_CHARACTERS", "0")

    assert load_settings().max_upload_size_bytes == 1024
    assert load_settings().max_request_size_bytes == 512
    assert load_settings().max_concurrent_document_validations == 3
    assert load_settings().upload_request_timeout_seconds == 45
    assert load_settings().max_documents_per_course == 4
    assert load_settings().max_course_storage_bytes == 5000
    assert load_settings().max_pdf_pages == 12
    assert load_settings().max_pdf_page_pixels == 1000
    assert load_settings().max_pdf_total_pixels == 2000
    assert load_settings().max_pdf_content_stream_bytes == 3000
    assert load_settings().max_pdf_drawing_operations == 4000
    assert load_settings().processing_job_lease_seconds == 90
    assert load_settings().processing_job_max_attempts == 5
    assert load_settings().processing_job_poll_seconds == 0.25
    assert load_settings().processing_job_attempt_timeout_seconds == 120
    assert load_settings().max_extracted_characters == 6000
    assert load_settings().max_document_chunks == 7
    assert load_settings().ocr_language == "eng+deu"
    assert load_settings().ocr_dpi == 400
    assert load_settings().ocr_min_text_characters == 0
    assert load_settings().document_chunk_size_characters == 500
    assert load_settings().document_chunk_overlap_characters == 0


@pytest.mark.parametrize("value", ["0", "-1", "not-a-number"])
def test_upload_limit_must_be_positive_integer(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("MAX_UPLOAD_SIZE_BYTES", value)

    with pytest.raises(ValueError, match="MAX_UPLOAD_SIZE_BYTES"):
        load_settings()


def test_pdf_page_limit_must_be_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_PDF_PAGES", "0")

    with pytest.raises(ValueError, match="MAX_PDF_PAGES"):
        load_settings()


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "not-a-number"])
def test_processing_poll_interval_must_be_positive_and_finite(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("PROCESSING_JOB_POLL_SECONDS", value)

    with pytest.raises(ValueError, match="PROCESSING_JOB_POLL_SECONDS"):
        load_settings()


@pytest.mark.parametrize(
    "name",
    [
        "PROCESSING_JOB_LEASE_SECONDS",
        "PROCESSING_JOB_MAX_ATTEMPTS",
        "PROCESSING_JOB_ATTEMPT_TIMEOUT_SECONDS",
        "UPLOAD_REQUEST_TIMEOUT_SECONDS",
        "MAX_EXTRACTED_CHARACTERS",
        "MAX_DOCUMENT_CHUNKS",
        "OCR_DPI",
        "DOCUMENT_CHUNK_SIZE_CHARACTERS",
    ],
)
def test_processing_integer_limits_must_be_positive(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    monkeypatch.setenv(name, "0")

    with pytest.raises(ValueError, match=name):
        load_settings()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("OCR_MIN_TEXT_CHARACTERS", "-1"),
        ("DOCUMENT_CHUNK_OVERLAP_CHARACTERS", "-1"),
    ],
)
def test_processing_nonnegative_settings_are_validated(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=name):
        load_settings()


def test_chunk_overlap_must_be_smaller_than_chunk_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOCUMENT_CHUNK_SIZE_CHARACTERS", "100")
    monkeypatch.setenv("DOCUMENT_CHUNK_OVERLAP_CHARACTERS", "100")

    with pytest.raises(ValueError, match="DOCUMENT_CHUNK_OVERLAP_CHARACTERS"):
        load_settings()


@pytest.mark.parametrize("value", ["", "eng fra", "+eng", "eng+"])
def test_ocr_language_expression_must_be_safe(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("OCR_LANGUAGE", value)

    with pytest.raises(ValueError, match="OCR_LANGUAGE"):
        load_settings()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PROCESSING_JOB_LEASE_SECONDS", "4"),
        ("PROCESSING_JOB_LEASE_SECONDS", "86401"),
        ("PROCESSING_JOB_MAX_ATTEMPTS", "101"),
        ("PROCESSING_JOB_ATTEMPT_TIMEOUT_SECONDS", "86401"),
        ("UPLOAD_REQUEST_TIMEOUT_SECONDS", "301"),
    ],
)
def test_processing_operational_limits_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=name):
        load_settings()


def test_hosted_mode_requires_postgres_secret_and_bootstrap_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEPLOYMENT_MODE", MODE_HOSTED)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///hosted.db")

    with pytest.raises(ValueError, match="PostgreSQL"):
        load_settings()

    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://lumina:password@localhost:5432/lumina",
    )
    monkeypatch.setenv("STORAGE_NAMESPACE", "hosted-shared-volume")
    with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
        load_settings()

    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 32)
    with pytest.raises(ValueError, match="BOOTSTRAP_ADMIN_EMAIL"):
        load_settings()

    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "admin@example.com")
    with pytest.raises(ValueError, match="BOOTSTRAP_ADMIN_TOKEN"):
        load_settings()

    monkeypatch.setenv("BOOTSTRAP_ADMIN_TOKEN", "y" * 32)
    loaded = load_settings()
    assert loaded.deployment_mode == MODE_HOSTED
    assert loaded.bootstrap_admin_email == "admin@example.com"
    assert loaded.bootstrap_admin_token == "y" * 32


def test_database_only_loader_does_not_require_runtime_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = "postgresql+psycopg://lumina:password@localhost:5432/lumina"
    monkeypatch.setenv("DEPLOYMENT_MODE", MODE_HOSTED)
    monkeypatch.setenv("DATABASE_URL", database_url)

    assert load_database_url() == database_url


def test_unavailable_postgresql_driver_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://localhost/lumina")

    with pytest.raises(ValueError, match="Unsupported database driver"):
        load_database_url()


def test_configured_jwt_secret_must_be_long_enough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "too-short")

    with pytest.raises(ValueError, match="at least 32"):
        load_settings()


def test_bootstrap_admin_email_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "@")

    with pytest.raises(ValueError, match="valid email"):
        load_settings()


@pytest.mark.parametrize("token", ["é" * 32, "x" * 31 + "\n", "x" * 31 + "\x7f"])
def test_bootstrap_token_must_be_header_safe_ascii(
    monkeypatch: pytest.MonkeyPatch,
    token: str,
) -> None:
    monkeypatch.setenv("BOOTSTRAP_ADMIN_TOKEN", token)

    with pytest.raises(ValueError, match="visible ASCII"):
        load_settings()


def test_gemini_api_key_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

    assert load_settings().gemini_api_key == "test-gemini-key"


def test_ai_provider_defaults_to_ollama() -> None:
    assert load_settings().ai_provider == "ollama"


def test_ai_provider_rejects_unsupported_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_PROVIDER", "unsupported")

    with pytest.raises(ValueError, match="AI_PROVIDER"):
        load_settings()
