"""Streaming hashes for uploaded documents."""

import hashlib

from fastapi import UploadFile

DEFAULT_CHUNK_SIZE = 1024 * 1024
FILE_HASH_ERROR_MESSAGE = "Unable to hash uploaded file."


class FileHashError(Exception):
    """Raised when an upload cannot be hashed or returned to its start."""

    def __init__(self, message: str = FILE_HASH_ERROR_MESSAGE):
        super().__init__(message)


def calculate_file_hash(
    upload: UploadFile,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> str:
    """Return an upload's SHA-256 digest without consuming its stream."""
    if type(chunk_size) is not int or chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer")

    try:
        stream = upload.file
    except Exception as exc:
        raise FileHashError() from exc

    try:
        try:
            stream.seek(0)
            digest = hashlib.sha256()

            while True:
                chunk = stream.read(chunk_size)
                if not isinstance(chunk, (bytes, bytearray, memoryview)):
                    raise TypeError("uploaded file stream must return bytes")
                if not chunk:
                    break
                digest.update(chunk)

            return digest.hexdigest()
        except Exception as exc:
            raise FileHashError() from exc
    finally:
        try:
            stream.seek(0)
        except Exception as exc:
            raise FileHashError() from exc
