"""S3-compatible object storage for uploaded documents."""

import re
from io import BytesIO
from typing import Any, BinaryIO
from uuid import UUID, uuid4

from botocore.config import Config
from botocore.exceptions import ClientError

from storage.base import (
    READINESS_PAYLOAD,
    Storage,
    StorageError,
    generate_portable_key,
    validate_portable_key,
)

_BUCKET_PATTERN = re.compile(r"(?!.*\.\.)[a-z0-9][a-z0-9.-]{2,62}")


def _error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


class S3Storage(Storage):
    """Store documents in an S3-compatible bucket.

    The boto3 client is injectable so tests can exercise the full
    contract against a fake without any live service. Without an
    injected client, credentials and the endpoint come from the
    constructor arguments or the standard AWS credential chain.
    """

    def __init__(
        self,
        bucket: str,
        *,
        client: Any | None = None,
        endpoint_url: str | None = None,
        region: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        force_path_style: bool = False,
        namespace: str = "default",
    ) -> None:
        if not isinstance(bucket, str) or not _BUCKET_PATTERN.fullmatch(bucket):
            raise ValueError(
                "bucket must be 3-63 lowercase letters, digits, dots, or dashes"
            )
        if type(force_path_style) is not bool:
            raise TypeError("force_path_style must be a boolean")

        self._bucket = bucket
        self._namespace = namespace
        if client is not None:
            self._client = client
        else:
            import boto3

            self._client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                region_name=region,
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key,
                config=Config(
                    s3={"addressing_style": ("path" if force_path_style else "auto")}
                ),
            )
        self.provider = f"s3:{namespace}"

    def check_ready(self) -> None:
        """Verify that the bucket supports durable writes."""
        probe_key = f"_readiness/{self._namespace}/{uuid4().hex}.probe"
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=probe_key,
                Body=READINESS_PAYLOAD,
            )
            response = self._client.get_object(Bucket=self._bucket, Key=probe_key)
            try:
                content = response["Body"].read()
            finally:
                response["Body"].close()
            if content != READINESS_PAYLOAD:
                raise StorageError("Document storage is not ready.")
            self._client.delete_object(Bucket=self._bucket, Key=probe_key)
        except StorageError:
            raise
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
        """Upload a binary stream and return it to position zero."""
        validate_portable_key(key)
        source_reset = False
        try:
            source.seek(0)
            self._client.put_object(Bucket=self._bucket, Key=key, Body=source)
            source.seek(0)
            source_reset = True
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError("Unable to save document.") from exc
        finally:
            if not source_reset:
                try:
                    source.seek(0)
                except Exception as exc:
                    raise StorageError(
                        "Unable to reset document source stream."
                    ) from exc

    def open(self, key: str) -> BinaryIO:
        """Open a stored document for binary reading."""
        validate_portable_key(key)
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            try:
                content = response["Body"].read()
            finally:
                response["Body"].close()
            return BytesIO(content)
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError("Unable to open stored document.") from exc

    def read(self, key: str) -> bytes:
        """Read and return all bytes for a stored document."""
        validate_portable_key(key)
        try:
            with self.open(key) as stored_file:
                return stored_file.read()
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError("Unable to read stored document.") from exc

    def delete(self, key: str) -> None:
        """Delete a stored document if it exists."""
        validate_portable_key(key)
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except StorageError:
            raise
        except ClientError as exc:
            if _error_code(exc) in {"NoSuchKey", "NotFound"}:
                return
            raise StorageError("Unable to delete stored document.") from exc
        except Exception as exc:
            raise StorageError("Unable to delete stored document.") from exc

    def exists(self, key: str) -> bool:
        """Return whether a key identifies a stored object."""
        validate_portable_key(key)
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except StorageError:
            raise
        except ClientError as exc:
            if _error_code(exc) in {"404", "NotFound", "NoSuchKey"}:
                return False
            raise StorageError("Unable to inspect stored document.") from exc
        except Exception as exc:
            raise StorageError("Unable to inspect stored document.") from exc
