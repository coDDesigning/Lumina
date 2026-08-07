import hashlib
from io import BytesIO

import pytest
from fastapi import UploadFile

from services.document_hash import FileHashError, calculate_file_hash


def make_upload(filename: str, content: bytes) -> UploadFile:
    return UploadFile(BytesIO(content), filename=filename)


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


class ReadFailureStream(BytesIO):
    def read(self, _size: int = -1) -> bytes:
        raise OSError("simulated read failure")


class SeekFailureStream(BytesIO):
    def seek(self, _offset: int, _whence: int = 0) -> int:
        raise OSError("simulated seek failure")


class ResetFailureStream(BytesIO):
    def __init__(self, content: bytes) -> None:
        super().__init__(content)
        self.seek_calls = 0

    def seek(self, offset: int, whence: int = 0) -> int:
        self.seek_calls += 1
        if self.seek_calls == 2:
            raise OSError("simulated reset failure")
        return super().seek(offset, whence)


def test_identical_content_produces_identical_hashes() -> None:
    content = b"same deterministic course material"

    first = calculate_file_hash(make_upload("notes.txt", content))
    second = calculate_file_hash(make_upload("notes.txt", content))

    assert first == second == hashlib.sha256(content).hexdigest()


def test_renaming_a_file_does_not_change_its_hash() -> None:
    content = b"the filename is not part of the digest"

    txt_hash = calculate_file_hash(make_upload("lesson.txt", content))
    markdown_hash = calculate_file_hash(make_upload("renamed.md", content))

    assert txt_hash == markdown_hash


def test_different_content_produces_different_hashes() -> None:
    assert calculate_file_hash(make_upload("a.txt", b"alpha")) != calculate_file_hash(
        make_upload("b.txt", b"beta")
    )


def test_large_file_is_read_in_bounded_chunks() -> None:
    chunk_size = 4096
    content = bytes(range(251)) * 200
    stream = TrackingStream(content)

    digest = calculate_file_hash(
        UploadFile(stream, filename="large.txt"),
        chunk_size=chunk_size,
    )

    assert digest == hashlib.sha256(content).hexdigest()
    assert len(stream.requested_sizes) > 2
    assert set(stream.requested_sizes) == {chunk_size}
    assert max(stream.returned_sizes) <= chunk_size


def test_hashing_leaves_stream_at_zero_and_readable() -> None:
    content = b"readable after hashing"
    stream = BytesIO(content)
    stream.seek(len(content))
    upload = UploadFile(stream, filename="notes.txt")

    calculate_file_hash(upload, chunk_size=3)

    assert stream.tell() == 0
    assert stream.read() == content


@pytest.mark.parametrize(
    "chunk_size",
    [0, -1, True, False, 1.5, "1024", None],
    ids=["zero", "negative", "true", "false", "float", "string", "none"],
)
def test_invalid_chunk_size_is_rejected(chunk_size: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        calculate_file_hash(make_upload("notes.txt", b"content"), chunk_size=chunk_size)  # type: ignore[arg-type]


def test_read_failure_is_wrapped_and_stream_is_reset() -> None:
    stream = ReadFailureStream(b"content")

    with pytest.raises(FileHashError) as raised:
        calculate_file_hash(UploadFile(stream, filename="notes.txt"))

    assert isinstance(raised.value.__cause__, OSError)
    assert stream.tell() == 0


def test_initial_seek_failure_is_wrapped() -> None:
    upload = UploadFile(SeekFailureStream(b"content"), filename="notes.txt")

    with pytest.raises(FileHashError) as raised:
        calculate_file_hash(upload)

    assert isinstance(raised.value.__cause__, OSError)


def test_final_reset_failure_overrides_a_successful_digest() -> None:
    upload = UploadFile(ResetFailureStream(b"content"), filename="notes.txt")

    with pytest.raises(FileHashError) as raised:
        calculate_file_hash(upload)

    assert isinstance(raised.value.__cause__, OSError)
