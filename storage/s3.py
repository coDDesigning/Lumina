"""S3-compatible object storage for uploaded documents."""

import re
from collections.abc import Iterator
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
_MAX_PROBE_VERSIONS = 10
_NOT_FOUND_CODES = {"404", "NoSuchKey", "NotFound"}


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
        self._endpoint_url = endpoint_url
        self._region = region
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._force_path_style = force_path_style
        self._injected_client = client is not None
        if client is not None:
            self._client = client
        else:
            self._client = self._create_boto_client()
        self.provider = f"s3:{namespace}"

    def _create_boto_client(self) -> Any:
        import boto3

        return boto3.client(
            "s3",
            endpoint_url=self._endpoint_url,
            region_name=self._region,
            aws_access_key_id=self._access_key_id,
            aws_secret_access_key=self._secret_access_key,
            config=Config(
                s3={"addressing_style": ("path" if self._force_path_style else "auto")}
            ),
        )

    def __getstate__(self) -> dict[str, Any]:
        state = self.__dict__.copy()
        if not self._injected_client:
            state["_client"] = None
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        self.__dict__.update(state)
        if self._client is None:
            self._client = self._create_boto_client()

    def check_ready(self) -> None:
        """Verify that the bucket supports durable writes."""
        probe_key = f"_readiness/{self._namespace}/{uuid4().hex}.probe"
        probe_attempted = False
        probe_version_id = None
        probe_failed = False
        try:
            probe_attempted = True
            put_response = self._client.put_object(
                Bucket=self._bucket,
                Key=probe_key,
                Body=READINESS_PAYLOAD,
            )
            probe_version_id = put_response.get("VersionId")
            response = self._client.get_object(Bucket=self._bucket, Key=probe_key)
            try:
                content = response["Body"].read()
            finally:
                response["Body"].close()
            if content != READINESS_PAYLOAD:
                raise StorageError("Document storage is not ready.")
        except StorageError:
            probe_failed = True
            raise
        except Exception as exc:
            probe_failed = True
            raise StorageError("Document storage is not ready.") from exc
        finally:
            if probe_attempted:
                try:
                    self._delete_readiness_probe(probe_key, probe_version_id)
                except Exception as exc:
                    if not probe_failed:
                        raise StorageError("Document storage is not ready.") from exc

    def _delete_readiness_probe(self, probe_key: str, version_id: str | None) -> None:
        for _ in range(_MAX_PROBE_VERSIONS):
            if version_id is None:
                try:
                    head_response = self._client.head_object(
                        Bucket=self._bucket, Key=probe_key
                    )
                except ClientError as exc:
                    if _error_code(exc) in _NOT_FOUND_CODES:
                        return
                    raise
                version_id = head_response.get("VersionId")

            delete_args = {"Bucket": self._bucket, "Key": probe_key}
            if version_id is not None:
                delete_args["VersionId"] = version_id
            self._client.delete_object(**delete_args)
            if version_id is None:
                return
            version_id = None

        try:
            self._client.head_object(Bucket=self._bucket, Key=probe_key)
        except ClientError as exc:
            if _error_code(exc) in _NOT_FOUND_CODES:
                return
            raise
        raise StorageError(
            "Document storage readiness cleanup exceeded its version limit."
        )

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

    def iter_chunks(self, key: str, chunk_size: int) -> Iterator[bytes]:
        """Stream an object body without buffering the complete object."""
        validate_portable_key(key)
        if type(chunk_size) is not int or chunk_size <= 0:
            raise ValueError("chunk_size must be a positive integer")
        body = None
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            body = response["Body"]
            while chunk := body.read(chunk_size):
                yield chunk
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError("Unable to stream stored document.") from exc
        finally:
            if body is not None:
                body.close()

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
