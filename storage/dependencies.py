"""FastAPI dependencies for document storage."""

from backend.app.config import (
    APP_ENV_PRODUCTION,
    STORAGE_BACKEND_LOCAL,
    STORAGE_BACKEND_S3,
    settings,
)
from storage.base import Storage
from storage.local import LocalStorage
from storage.s3 import S3Storage


def _build_storage() -> Storage:
    if settings.storage_backend == STORAGE_BACKEND_LOCAL:
        return LocalStorage(
            settings.upload_directory,
            namespace=settings.storage_namespace,
            require_existing_root=settings.app_env == APP_ENV_PRODUCTION,
        )
    if settings.storage_backend == STORAGE_BACKEND_S3:
        return S3Storage(
            settings.s3_bucket or "",
            endpoint_url=settings.s3_endpoint_url,
            region=settings.s3_region,
            access_key_id=settings.s3_access_key_id,
            secret_access_key=settings.s3_secret_access_key,
            force_path_style=settings.s3_force_path_style,
            namespace=settings.storage_namespace,
        )
    raise RuntimeError(f"Unsupported storage backend: {settings.storage_backend}")


_storage = _build_storage()


def get_storage() -> Storage:
    """Return the process-wide storage provider."""
    return _storage
