from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import pytest

import storage.local as local_storage_module
from storage.base import Storage, StorageError
from storage.local import LocalStorage


class TrackingStream(BytesIO):
    def __init__(self, content: bytes) -> None:
        super().__init__(content)
        self.requested_sizes: list[int] = []
        self.returned_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.requested_sizes.append(size)
        chunk = super().read(size)
        self.returned_sizes.append(len(chunk))
        return chunk


class ResetFailureStream(BytesIO):
    def __init__(self, content: bytes) -> None:
        super().__init__(content)
        self.seek_calls = 0

    def seek(self, offset: int, whence: int = 0) -> int:
        self.seek_calls += 1
        if self.seek_calls == 2:
            raise OSError("simulated reset failure")
        return super().seek(offset, whence)


def test_generate_key_is_canonical_and_portable(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    document_id = UUID("12345678-1234-5678-9234-567812345678")

    key = storage.generate_key(42, str(document_id).upper(), "markdown")

    assert key == (
        "courses/42/documents/12345678-1234-5678-9234-567812345678/source.markdown"
    )
    assert "\\" not in key
    assert not key.startswith("/")
    assert isinstance(storage, Storage)


@pytest.mark.parametrize("chunk_size", [0, -1, True, 2.5])
def test_invalid_storage_chunk_size_is_rejected(
    tmp_path: Path,
    chunk_size: object,
) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        LocalStorage(tmp_path, chunk_size=chunk_size)  # type: ignore[arg-type]


def test_chunked_save_read_open_exists_and_delete(tmp_path: Path) -> None:
    chunk_size = 31
    content = bytes(range(251)) * 5
    source = TrackingStream(content)
    source.seek(len(content))
    storage = LocalStorage(tmp_path, chunk_size=chunk_size)
    key = storage.generate_key(7, uuid4(), "txt")

    assert storage.exists(key) is False
    storage.save(key, source)

    assert source.tell() == 0
    assert len(source.requested_sizes) > 2
    assert set(source.requested_sizes) == {chunk_size}
    assert max(source.returned_sizes) <= chunk_size
    assert storage.exists(key) is True
    assert storage.read(key) == content
    with storage.open(key) as stored_file:
        assert stored_file.read(9) == content[:9]

    storage.delete(key)
    assert storage.exists(key) is False
    storage.delete(key)


def test_readiness_probe_leaves_no_files(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)

    storage.check_ready()

    assert list(tmp_path.iterdir()) == []


def test_readiness_probe_creates_development_storage_root(tmp_path: Path) -> None:
    root = tmp_path / "missing" / "uploads"
    storage = LocalStorage(root)

    storage.check_ready()

    assert root.is_dir()
    assert list(root.iterdir()) == []


def test_readiness_probe_rejects_missing_storage_root(tmp_path: Path) -> None:
    root = tmp_path / "missing" / "uploads"
    storage = LocalStorage(root, require_existing_root=True)

    with pytest.raises(StorageError, match="not ready"):
        storage.check_ready()

    assert not root.exists()


def test_strict_save_does_not_recreate_missing_storage_root(tmp_path: Path) -> None:
    root = tmp_path / "missing" / "uploads"
    storage = LocalStorage(root, require_existing_root=True)
    source = BytesIO(b"document")
    key = storage.generate_key(1, uuid4(), "txt")

    with pytest.raises(StorageError, match="Unable to save document"):
        storage.save(key, source)

    assert source.tell() == 0
    assert not root.exists()


def test_readiness_probe_failure_is_wrapped_and_cleaned_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = LocalStorage(tmp_path)

    def fail_fsync(_file_descriptor: int) -> None:
        raise OSError("simulated readiness failure")

    monkeypatch.setattr(local_storage_module.os, "fsync", fail_fsync)

    with pytest.raises(StorageError, match="not ready"):
        storage.check_ready()

    assert list(tmp_path.iterdir()) == []


def test_readiness_probe_rejects_file_as_storage_root(tmp_path: Path) -> None:
    root = tmp_path / "uploads"
    root.write_bytes(b"not a directory")
    storage = LocalStorage(root)

    with pytest.raises(StorageError, match="not ready"):
        storage.check_ready()


@pytest.mark.parametrize(
    "key",
    [
        "../escape.txt",
        "courses/1/../../escape.txt",
        "/absolute/source.txt",
        "//server/share/source.txt",
        "C:/absolute/source.txt",
        r"courses\1\documents\source.txt",
        r"C:\absolute\source.txt",
        "courses//source.txt",
    ],
    ids=[
        "parent",
        "nested-parent",
        "posix-absolute",
        "network-absolute",
        "drive-absolute",
        "backslashes",
        "drive-backslashes",
        "empty-component",
    ],
)
def test_unsafe_keys_are_rejected(tmp_path: Path, key: str) -> None:
    storage = LocalStorage(tmp_path)

    with pytest.raises(ValueError):
        storage.exists(key)


def test_atomic_replace_failure_preserves_destination_and_removes_temp_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = LocalStorage(tmp_path, chunk_size=4)
    key = storage.generate_key(3, uuid4(), "txt")
    storage.save(key, BytesIO(b"existing destination"))
    replacement = BytesIO(b"new content")

    def fail_replace(_source: object, _destination: object) -> None:
        raise OSError("simulated atomic replace failure")

    monkeypatch.setattr(local_storage_module.os, "replace", fail_replace)

    with pytest.raises(StorageError, match="Unable to save document"):
        storage.save(key, replacement)

    assert replacement.tell() == 0
    assert storage.read(key) == b"existing destination"
    assert [path for path in tmp_path.rglob("*") if path.suffix == ".tmp"] == []


def test_source_reset_failure_happens_before_installing_destination(
    tmp_path: Path,
) -> None:
    storage = LocalStorage(tmp_path, chunk_size=4)
    key = storage.generate_key(3, uuid4(), "txt")
    storage.save(key, BytesIO(b"existing destination"))

    with pytest.raises(StorageError, match="Unable to save document"):
        storage.save(key, ResetFailureStream(b"new content"))

    assert storage.read(key) == b"existing destination"
    assert [path for path in tmp_path.rglob("*") if path.suffix == ".tmp"] == []
