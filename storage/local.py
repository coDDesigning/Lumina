"""Local filesystem document storage."""

import os
import stat
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

from storage.base import (
    READINESS_PAYLOAD,
    Storage,
    StorageError,
    generate_portable_key,
    validate_portable_key,
)

DEFAULT_CHUNK_SIZE = 1024 * 1024
DIRECTORY_CREATE_ATTEMPTS = 3


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
                if probe.write(READINESS_PAYLOAD) != len(READINESS_PAYLOAD):
                    raise OSError("incomplete readiness probe write")
                probe.flush()
                os.fsync(probe.fileno())
                probe.seek(0)
                if probe.read() != READINESS_PAYLOAD:
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
        return generate_portable_key(course_id, document_uuid, validated_file_type)

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

    def iter_chunks(self, key: str, chunk_size: int) -> Iterator[bytes]:
        """Stream a stored document without loading it all into memory."""
        if type(chunk_size) is not int or chunk_size <= 0:
            raise ValueError("chunk_size must be a positive integer")
        try:
            with self.open(key) as stored_file:
                while chunk := stored_file.read(chunk_size):
                    yield chunk
        except StorageError:
            raise
        except OSError as exc:
            raise StorageError("Unable to stream stored document.") from exc

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
        """Delete a stored document if it exists, and the directories it created."""
        try:
            path = self._path_for_key(key)
            path.unlink(missing_ok=True)
        except StorageError:
            raise
        except OSError as exc:
            raise StorageError("Unable to delete stored document.") from exc

        self._prune_document_directory(path.parent)

    def _prune_document_directory(self, directory: Path) -> None:
        """Remove the directories this key emptied, up to but never including the root.

        A key is courses/<course>/documents/<uuid>/<file>, so an upload creates a
        directory of its own per document plus the shared course levels. Pruning
        only the per-document directory would still leave courses/<course>/documents/
        and courses/<course>/ behind for every course whose last document is deleted,
        so the walk continues upwards and stops at the first directory that is not
        empty, is not below the root, or is the root itself. An upload rebuilds any
        level it needs and retries the components a concurrent prune removed under it,
        so pruning a shared level cannot fail a concurrent upload. Pruning is best
        effort and never fails a deletion that has already happened.
        """
        current = directory
        while True:
            try:
                if current == self.root or not current.is_relative_to(self.root):
                    return
                if current.is_symlink() or not current.is_dir():
                    return
                current.rmdir()
            except OSError:
                return
            current = current.parent

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
        validate_portable_key(key)

        try:
            path = self.root.joinpath(*key.split("/")).resolve()
        except (OSError, RuntimeError) as exc:
            raise StorageError("Unable to resolve document storage key.") from exc

        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("storage key escapes the configured root") from exc

        return path

    def _prepare_destination_parent(self, destination: Path) -> None:
        # A concurrent delete prunes the shared levels it empties, so a component
        # this build already created can disappear before the next one is made.
        # The tree is rebuilt rather than reported as a failed upload.
        for attempt in range(DIRECTORY_CREATE_ATTEMPTS):
            try:
                self._create_destination_parent(destination)
                return
            except FileNotFoundError:
                if attempt + 1 == DIRECTORY_CREATE_ATTEMPTS:
                    raise

    def _create_destination_parent(self, destination: Path) -> None:
        if not self._require_existing_root:
            destination.parent.mkdir(parents=True, exist_ok=True)
            return
        if not self.root.is_dir():
            raise OSError("document storage root does not exist")

        current = self.root
        for part in destination.parent.relative_to(self.root).parts:
            current /= part
            current.mkdir(exist_ok=True)
