"""Document storage abstractions and local implementation."""

from storage.base import Storage, StorageError
from storage.local import LocalStorage

__all__ = ["LocalStorage", "Storage", "StorageError"]
