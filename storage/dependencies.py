"""FastAPI dependencies for document storage."""

from backend.app.config import STORAGE_BACKEND_LOCAL, settings
from storage.base import Storage
from storage.local import LocalStorage


def _build_storage() -> Storage:
    if settings.storage_backend == STORAGE_BACKEND_LOCAL:
        return LocalStorage(
            settings.upload_directory,
            namespace=settings.storage_namespace,
        )
    raise RuntimeError(f"Unsupported storage backend: {settings.storage_backend}")


_storage = _build_storage()


def get_storage() -> Storage:
    """Return the process-wide storage provider."""
    return _storage
