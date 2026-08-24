"""In-memory generation lock management for active documents.

Prevents documents from being deleted while they supply active generation context.
Multiple generations can concurrently hold shared read locks on the same document.
"""

from collections import defaultdict
from collections.abc import Generator, Iterable
from contextlib import contextmanager
import threading
from uuid import UUID


class DocumentLockManager:
    """Thread-safe manager for document generation locks."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active_generations: dict[UUID, int] = defaultdict(int)

    def acquire(self, document_ids: Iterable[UUID]) -> None:
        unique_ids = set(document_ids)
        if not unique_ids:
            return
        with self._lock:
            for doc_id in unique_ids:
                self._active_generations[doc_id] += 1

    def release(self, document_ids: Iterable[UUID]) -> None:
        unique_ids = set(document_ids)
        if not unique_ids:
            return
        with self._lock:
            for doc_id in unique_ids:
                current = self._active_generations.get(doc_id, 0)
                if current <= 1:
                    self._active_generations.pop(doc_id, None)
                else:
                    self._active_generations[doc_id] = current - 1

    def is_locked(self, document_id: UUID) -> bool:
        with self._lock:
            return self._active_generations.get(document_id, 0) > 0

    def reset(self) -> None:
        with self._lock:
            self._active_generations.clear()


_lock_manager = DocumentLockManager()


@contextmanager
def acquire_generation_locks(
    document_ids: Iterable[UUID],
) -> Generator[None, None, None]:
    """Context manager to acquire and release generation locks on documents."""
    doc_id_set = set(document_ids)
    _lock_manager.acquire(doc_id_set)
    try:
        yield
    finally:
        _lock_manager.release(doc_id_set)


def is_document_locked_for_generation(document_id: UUID) -> bool:
    """Check if a document is currently locked by any active generation."""
    return _lock_manager.is_locked(document_id)


def reset_generation_locks() -> None:
    """Reset all active document generation locks (primarily for tests)."""
    _lock_manager.reset()
