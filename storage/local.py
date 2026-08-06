"""Local filesystem document storage."""

import os
import re
import stat
import tempfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from uuid import UUID

from storage.base import Storage, StorageError

DEFAULT_CHUNK_SIZE = 1024 * 1024
_FILE_TYPE_PATTERN = re.compile(r"[a-z0-9]+")
_KEY_PART_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_READINESS_PAYLOAD = b"lumina-storage-readiness"


class LocalStorage(Storage):
    """Store documents beneath a configured local filesystem root."""

    def __init__(
        self,
        root: str | os.PathLike[str],
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        namespace: str = "default",
        require_existing_root: bool = False,
    ) -> None:
        if type(chunk_size) is not int or chunk_size <= 0:
            raise ValueError("chunk_size must be a positive integer")
        if type(require_existing_root) is not bool:
            raise TypeError("require_existing_root must be a boolean")

        try:
            self.root = Path(root).expanduser().resolve()
        except (OSError, RuntimeError) as exc:
            raise StorageError("Unable to initialize local document storage.") from exc
        self._chunk_size = chunk_size
        self._require_existing_root = require_existing_root
        self.provider = f"local:{namespace}"

    def check_ready(self) -> None:
        """Verify that the storage root supports durable temporary writes."""
        try:
            if not self.root.is_dir():
                if self._require_existing_root:
                    raise OSError("document storage root does not exist")
                self.root.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w+b",
                dir=self.root,
                prefix=".lumina-readiness-",
                suffix=".tmp",
            ) as probe:
                if probe.write(_READINESS_PAYLOAD) != len(_READINESS_PAYLOAD):
                    raise OSError("incomplete readiness probe write")
                probe.flush()
                os.fsync(probe.fileno())
                probe.seek(0)
                if probe.read() != _READINESS_PAYLOAD:
                    raise OSError("readiness probe content mismatch")
        except Exception as exc:
            raise StorageError("Document storage is not ready.") from exc

    def generate_key(
        self,
        course_id: int,
        document_uuid: UUID | str,
        validated_file_type: str,
    ) -> str:
        """Generate a canonical key without using the original filename."""
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

    def save(self, key: str, source: BinaryIO) -> None:
        """Atomically save a binary stream and return it to position zero."""
        temporary_path: Path | None = None
        source_reset = False

        try:
            destination = self._path_for_key(key)
            source.seek(0)
            self._prepare_destination_parent(destination)

            with tempfile.NamedTemporaryFile(
                mode="w+b",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)

                while True:
                    chunk = source.read(self._chunk_size)
                    if not isinstance(chunk, (bytes, bytearray, memoryview)):
                        raise TypeError("source stream must return bytes")
                    if not chunk:
                        break

                    written = temporary_file.write(chunk)
                    if written != len(chunk):
                        raise OSError("incomplete temporary file write")

                temporary_file.flush()
                os.fsync(temporary_file.fileno())

            # Reset before installation so reset failure cannot orphan a final file.
            source.seek(0)
            source_reset = True
            os.replace(temporary_path, destination)
            temporary_path = None
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError("Unable to save document.") from exc
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except Exception as exc:
                    raise StorageError(
                        "Unable to clean up temporary document storage."
                    ) from exc
            if not source_reset:
                try:
                    source.seek(0)
                except Exception as exc:
                    raise StorageError(
                        "Unable to reset document source stream."
                    ) from exc

    def open(self, key: str) -> BinaryIO:
        """Open a stored document for binary reading."""
        try:
            return self._path_for_key(key).open("rb")
        except StorageError:
            raise
        except OSError as exc:
            raise StorageError("Unable to open stored document.") from exc

    def read(self, key: str) -> bytes:
        """Read and return all bytes for a stored document."""
        try:
            with self.open(key) as stored_file:
                return stored_file.read()
        except StorageError:
            raise
        except OSError as exc:
            raise StorageError("Unable to read stored document.") from exc

    def delete(self, key: str) -> None:
        """Delete a stored document if it exists."""
        try:
            self._path_for_key(key).unlink(missing_ok=True)
        except StorageError:
            raise
        except OSError as exc:
            raise StorageError("Unable to delete stored document.") from exc

    def exists(self, key: str) -> bool:
        """Return whether a key identifies a regular stored file."""
        try:
            file_status = self._path_for_key(key).stat()
        except (FileNotFoundError, NotADirectoryError):
            return False
        except StorageError:
            raise
        except OSError as exc:
            raise StorageError("Unable to inspect stored document.") from exc

        return stat.S_ISREG(file_status.st_mode)

    def _path_for_key(self, key: str) -> Path:
        if not isinstance(key, str) or not key:
            raise ValueError("storage key must be a non-empty portable path")
        if "\\" in key or PurePosixPath(key).is_absolute():
            raise ValueError("storage key must be a relative portable path")

        parts = key.split("/")
        if any(
            part in {"", ".", ".."} or not _KEY_PART_PATTERN.fullmatch(part)
            for part in parts
        ):
            raise ValueError("storage key contains an unsafe path component")

        try:
            path = self.root.joinpath(*parts).resolve()
        except (OSError, RuntimeError) as exc:
            raise StorageError("Unable to resolve document storage key.") from exc

        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("storage key escapes the configured root") from exc

        return path

    def _prepare_destination_parent(self, destination: Path) -> None:
        if not self._require_existing_root:
            destination.parent.mkdir(parents=True, exist_ok=True)
            return
        if not self.root.is_dir():
            raise OSError("document storage root does not exist")

        current = self.root
        for part in destination.parent.relative_to(self.root).parts:
            current /= part
            current.mkdir(exist_ok=True)
