import pytest

from backend.app.config import (
    DEFAULT_MAX_CONCURRENT_DOCUMENT_VALIDATIONS,
    DEFAULT_MAX_COURSE_STORAGE_BYTES,
    DEFAULT_MAX_DOCUMENTS_PER_COURSE,
    DEFAULT_MAX_PDF_CONTENT_STREAM_BYTES,
    DEFAULT_MAX_PDF_DRAWING_OPERATIONS,
    DEFAULT_MAX_PDF_PAGE_PIXELS,
    DEFAULT_MAX_PDF_PAGES,
    DEFAULT_MAX_PDF_TOTAL_PIXELS,
    DEFAULT_MAX_REQUEST_SIZE_BYTES,
    DEFAULT_MAX_UPLOAD_SIZE_BYTES,
    MODE_HOSTED,
    MODE_SELF_HOSTED,
    load_settings,
)
from backend.app.database_config import load_database_url

CONFIGURATION_KEYS = (
    "DEPLOYMENT_MODE",
    "DATABASE_URL",
    "STORAGE_BACKEND",
    "STORAGE_NAMESPACE",
    "UPLOAD_DIRECTORY",
    "CHROMA_PERSIST_DIRECTORY",
    "JWT_SECRET_KEY",
    "BOOTSTRAP_ADMIN_EMAIL",
    "BOOTSTRAP_ADMIN_TOKEN",
    "MAX_UPLOAD_SIZE_BYTES",
    "MAX_REQUEST_SIZE_BYTES",
    "MAX_CONCURRENT_DOCUMENT_VALIDATIONS",
    "MAX_DOCUMENTS_PER_COURSE",
    "MAX_COURSE_STORAGE_BYTES",
    "MAX_PDF_PAGES",
    "MAX_PDF_PAGE_PIXELS",
    "MAX_PDF_TOTAL_PIXELS",
    "MAX_PDF_CONTENT_STREAM_BYTES",
    "MAX_PDF_DRAWING_OPERATIONS",
)


@pytest.fixture(autouse=True)
def clear_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in CONFIGURATION_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_self_hosted_defaults_are_safe_and_runnable() -> None:
    loaded = load_settings()

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
    assert loaded.max_documents_per_course == DEFAULT_MAX_DOCUMENTS_PER_COURSE
    assert loaded.max_course_storage_bytes == DEFAULT_MAX_COURSE_STORAGE_BYTES
    assert loaded.max_pdf_pages == DEFAULT_MAX_PDF_PAGES
    assert loaded.max_pdf_page_pixels == DEFAULT_MAX_PDF_PAGE_PIXELS
    assert loaded.max_pdf_total_pixels == DEFAULT_MAX_PDF_TOTAL_PIXELS
    assert loaded.max_pdf_content_stream_bytes == DEFAULT_MAX_PDF_CONTENT_STREAM_BYTES
    assert loaded.max_pdf_drawing_operations == DEFAULT_MAX_PDF_DRAWING_OPERATIONS


def test_upload_limit_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_UPLOAD_SIZE_BYTES", "1024")
    monkeypatch.setenv("MAX_REQUEST_SIZE_BYTES", "512")
    monkeypatch.setenv("MAX_CONCURRENT_DOCUMENT_VALIDATIONS", "3")
    monkeypatch.setenv("MAX_DOCUMENTS_PER_COURSE", "4")
    monkeypatch.setenv("MAX_COURSE_STORAGE_BYTES", "5000")
    monkeypatch.setenv("MAX_PDF_PAGES", "12")
    monkeypatch.setenv("MAX_PDF_PAGE_PIXELS", "1000")
    monkeypatch.setenv("MAX_PDF_TOTAL_PIXELS", "2000")
    monkeypatch.setenv("MAX_PDF_CONTENT_STREAM_BYTES", "3000")
    monkeypatch.setenv("MAX_PDF_DRAWING_OPERATIONS", "4000")

    assert load_settings().max_upload_size_bytes == 1024
    assert load_settings().max_request_size_bytes == 512
    assert load_settings().max_concurrent_document_validations == 3
    assert load_settings().max_documents_per_course == 4
    assert load_settings().max_course_storage_bytes == 5000
    assert load_settings().max_pdf_pages == 12
    assert load_settings().max_pdf_page_pixels == 1000
    assert load_settings().max_pdf_total_pixels == 2000
    assert load_settings().max_pdf_content_stream_bytes == 3000
    assert load_settings().max_pdf_drawing_operations == 4000


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
