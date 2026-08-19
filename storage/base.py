"""Interfaces shared by document storage providers."""

import re
from typing import BinaryIO, Protocol, runtime_checkable
from uuid import UUID

READINESS_PAYLOAD = b"lumina-storage-readiness"
_FILE_TYPE_PATTERN = re.compile(r"[a-z0-9]+")
_KEY_PART_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class StorageError(Exception):
    """A storage operation failed without exposing provider details."""

    def __init__(self, message: str = "Document storage operation failed."):
        super().__init__(message)


def generate_portable_key(
    course_id: int,
    document_uuid: UUID | str,
    validated_file_type: str,
) -> str:
    """Generate the canonical storage key without using the original filename."""
    if type(course_id) is not int or course_id <= 0:
        raise ValueError("course_id must be a positive integer")
    if not isinstance(validated_file_type, str) or not _FILE_TYPE_PATTERN.fullmatch(
        validated_file_type
    ):
        raise ValueError(
            "validated_file_type must contain only lowercase letters and digits"
        )

    if not isinstance(document_uuid, (UUID, str)):
        raise TypeError("document_uuid must be a UUID")
    try:
        normalized_document_uuid = UUID(str(document_uuid))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("document_uuid must be a UUID") from exc

    return (
        f"courses/{course_id}/documents/{normalized_document_uuid}/"
        f"source.{validated_file_type}"
    )


def validate_portable_key(key: str) -> str:
    """Validate a portable relative storage key shared across providers."""
    if not isinstance(key, str) or not key:
        raise ValueError("storage key must be a non-empty portable path")
    if "\\" in key or key.startswith("/"):
        raise ValueError("storage key must be a relative portable path")

    parts = key.split("/")
    if any(
        part in {"", ".", ".."} or not _KEY_PART_PATTERN.fullmatch(part)
        for part in parts
    ):
        raise ValueError("storage key contains an unsafe path component")
    return key


@runtime_checkable
class Storage(Protocol):
    """Synchronous document storage provider contract."""

    provider: str

    def check_ready(self) -> None: ...

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
