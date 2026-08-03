"""Interfaces shared by document storage providers."""

from typing import BinaryIO, Protocol, runtime_checkable
from uuid import UUID


class StorageError(Exception):
    """A storage operation failed without exposing provider details."""

    def __init__(self, message: str = "Document storage operation failed."):
        super().__init__(message)


@runtime_checkable
class Storage(Protocol):
    """Synchronous document storage provider contract."""

    provider: str

    def generate_key(
        self,
        course_id: int,
        document_uuid: UUID | str,
        validated_file_type: str,
    ) -> str: ...

    def save(self, key: str, source: BinaryIO) -> None: ...

    def open(self, key: str) -> BinaryIO: ...

    def read(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...

    def exists(self, key: str) -> bool: ...
