from io import BytesIO
from typing import Any
from uuid import UUID, uuid4

import pytest
from botocore.exceptions import ClientError

import storage.dependencies as storage_dependencies
from backend.app.config import load_settings
from storage.base import Storage, StorageError
from storage.s3 import S3Storage


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "FakeOperation")


class FakeS3Client:
    """In-memory S3 client implementing the exact calls S3Storage makes."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.put_object_calls: list[tuple[str, str]] = []
        self.delete_object_calls: list[tuple[str, str]] = []
        self.delete_object_versions: list[str | None] = []
        self.fail_put_object_with: str | None = None
        self.fail_put_object_after_write_with: str | None = None
        self.fail_get_object_with: str | None = None
        self.return_wrong_content = False
        self.version_id: str | None = None
        self.version_ids: list[str] = []
        self.read_sizes: list[int] = []

    def put_object(self, *, Bucket: str, Key: str, Body: object) -> dict:
        self.put_object_calls.append((Bucket, Key))
        if self.fail_put_object_with is not None:
            raise _client_error(self.fail_put_object_with)
        if hasattr(Body, "read"):
            Body.seek(0)  # type: ignore[union-attr]
            content = Body.read()
        else:
            content = Body
        if not isinstance(content, bytes):
            raise TypeError("Body must be bytes or a binary stream")
        self.objects[(Bucket, Key)] = content
        if self.fail_put_object_after_write_with is not None:
            raise _client_error(self.fail_put_object_after_write_with)
        version_id = self.version_ids[-1] if self.version_ids else self.version_id
        return {"VersionId": version_id} if version_id is not None else {}

    def get_object(self, *, Bucket: str, Key: str) -> dict:
        if self.fail_get_object_with is not None:
            raise _client_error(self.fail_get_object_with)
        try:
            content = self.objects[(Bucket, Key)]
        except KeyError as exc:
            raise _client_error("NoSuchKey") from exc
        client = self

        class RecordingBody(BytesIO):
            def read(self, size=-1):
                client.read_sizes.append(size)
                return super().read(size)

        return {
            "Body": RecordingBody(b"wrong" if self.return_wrong_content else content)
        }

    def head_object(self, *, Bucket: str, Key: str) -> dict:
        if (Bucket, Key) not in self.objects:
            raise _client_error("404")
        version_id = self.version_ids[-1] if self.version_ids else self.version_id
        return {"VersionId": version_id} if version_id is not None else {}

    def delete_object(
        self, *, Bucket: str, Key: str, VersionId: str | None = None
    ) -> dict:
        self.delete_object_calls.append((Bucket, Key))
        self.delete_object_versions.append(VersionId)
        if self.version_ids and VersionId in self.version_ids:
            self.version_ids.remove(VersionId)
            if self.version_ids:
                return {}
        self.objects.pop((Bucket, Key), None)
        return {}


def _s3_storage(
    client: FakeS3Client,
    namespace: str = "test",
    **kwargs: Any,
) -> S3Storage:
    return S3Storage("lumina", client=client, namespace=namespace, **kwargs)


def test_generate_key_is_canonical_and_portable() -> None:
    storage = _s3_storage(FakeS3Client())
    document_id = UUID("12345678-1234-5678-9234-567812345678")

    key = storage.generate_key(42, str(document_id).upper(), "markdown")

    assert key == (
        "courses/42/documents/12345678-1234-5678-9234-567812345678/source.markdown"
    )
    assert "\\" not in key
    assert not key.startswith("/")
    assert isinstance(storage, Storage)


@pytest.mark.parametrize(
    "bucket",
    ["", "Ab", "ab", "a" * 64, "a_b", "bucket..double-dot"],
)
def test_invalid_bucket_is_rejected(bucket: str) -> None:
    with pytest.raises(ValueError, match="bucket"):
        S3Storage(bucket, client=FakeS3Client())


@pytest.mark.parametrize("force_path_style", [0, 1, "true", None])
def test_force_path_style_must_be_boolean(force_path_style: object) -> None:
    with pytest.raises(TypeError, match="boolean"):
        S3Storage(
            "lumina",
            client=FakeS3Client(),
            force_path_style=force_path_style,  # type: ignore[arg-type]
        )


def test_provider_is_attributed_to_namespace() -> None:
    storage = _s3_storage(FakeS3Client(), namespace="hosted-shared")

    assert storage.provider == "s3:hosted-shared"


def test_save_read_open_exists_and_delete() -> None:
    client = FakeS3Client()
    storage = _s3_storage(client)
    content = bytes(range(251)) * 5
    source = BytesIO(content)
    source.seek(len(content))
    key = storage.generate_key(7, uuid4(), "txt")

    assert storage.exists(key) is False
    storage.save(key, source)

    assert source.tell() == 0
    assert (client.objects[(storage._bucket, key)]) == content
    assert storage.exists(key) is True
    assert storage.read(key) == content
    with storage.open(key) as stored_file:
        assert stored_file.read(9) == content[:9]
    client.read_sizes.clear()
    assert b"".join(storage.iter_chunks(key, 9)) == content
    assert set(client.read_sizes) == {9}

    storage.delete(key)
    assert storage.exists(key) is False


def test_check_ready_verifies_probe_round_trip() -> None:
    client = FakeS3Client()
    storage = _s3_storage(client, namespace="readiness-ns")

    storage.check_ready()

    assert len(client.put_object_calls) == 1
    assert len(client.delete_object_calls) == 1
    assert client.put_object_calls[0][0] == "lumina"
    assert client.put_object_calls[0][1].startswith("_readiness/readiness-ns/")
    assert client.delete_object_calls[0][0] == client.put_object_calls[0][0]
    assert client.delete_object_calls[0][1] == client.put_object_calls[0][1]
    assert not client.objects


def test_check_ready_failure_is_wrapped() -> None:
    client = FakeS3Client()
    client.fail_put_object_with = "AccessDenied"
    storage = _s3_storage(client)

    with pytest.raises(StorageError, match="not ready"):
        storage.check_ready()


def test_check_ready_cleans_up_after_an_ambiguous_write_failure() -> None:
    client = FakeS3Client()
    client.version_id = "ambiguous-version"
    client.fail_put_object_after_write_with = "RequestTimeout"
    storage = _s3_storage(client)

    with pytest.raises(StorageError, match="not ready"):
        storage.check_ready()

    assert len(client.delete_object_calls) == 1
    assert client.delete_object_versions == ["ambiguous-version"]
    assert not client.objects


def test_check_ready_deletes_the_written_object_version() -> None:
    client = FakeS3Client()
    client.version_id = "probe-version"
    storage = _s3_storage(client)

    storage.check_ready()

    assert client.delete_object_versions == ["probe-version"]


def test_check_ready_deletes_every_version_committed_by_retries() -> None:
    client = FakeS3Client()
    client.version_ids = ["first-attempt", "retry-attempt"]
    storage = _s3_storage(client)

    storage.check_ready()

    assert client.delete_object_versions == ["retry-attempt", "first-attempt"]
    assert not client.objects


def test_check_ready_deletes_probe_when_read_fails() -> None:
    client = FakeS3Client()
    client.fail_get_object_with = "ServiceUnavailable"
    storage = _s3_storage(client)

    with pytest.raises(StorageError, match="not ready"):
        storage.check_ready()

    assert len(client.delete_object_calls) == 1
    assert not client.objects


def test_check_ready_deletes_probe_when_content_mismatches() -> None:
    client = FakeS3Client()
    client.return_wrong_content = True
    storage = _s3_storage(client)

    with pytest.raises(StorageError, match="not ready"):
        storage.check_ready()

    assert len(client.delete_object_calls) == 1
    assert not client.objects


def test_save_failure_is_wrapped_and_source_reset() -> None:
    client = FakeS3Client()
    client.fail_put_object_with = "AccessDenied"
    storage = _s3_storage(client)
    source = BytesIO(b"content")
    source.seek(0)

    with pytest.raises(StorageError, match="save"):
        storage.save("courses/1/documents/x/source.txt", source)

    assert source.tell() == 0


def test_open_missing_key_is_wrapped() -> None:
    storage = _s3_storage(FakeS3Client())

    with pytest.raises(StorageError, match="open"):
        storage.open("courses/1/documents/x/source.txt")


def test_read_missing_key_is_wrapped() -> None:
    storage = _s3_storage(FakeS3Client())

    with pytest.raises(StorageError, match="Unable to open"):
        storage.read("courses/1/documents/x/source.txt")


def test_delete_missing_key_is_tolerated() -> None:
    storage = _s3_storage(FakeS3Client())

    storage.delete("courses/1/documents/x/source.txt")


def test_exists_returns_false_for_missing_key() -> None:
    storage = _s3_storage(FakeS3Client())

    assert storage.exists("courses/1/documents/x/source.txt") is False


def test_unexpected_client_error_is_wrapped() -> None:
    client = FakeS3Client()
    storage = _s3_storage(client)

    client.objects[("lumina", "courses/1/documents/x/source.txt")] = b"content"

    def failing_head_object(*, Bucket: str, Key: str) -> dict:
        raise _client_error("403")

    client.head_object = failing_head_object  # type: ignore[method-assign]

    with pytest.raises(StorageError, match="inspect"):
        storage.exists("courses/1/documents/x/source.txt")


@pytest.mark.parametrize(
    "key",
    ["", "../escape", "/absolute", "a\\b", "unsafe/../component", "a//b"],
)
def test_unsafe_keys_are_rejected_across_operations(key: str) -> None:
    storage = _s3_storage(FakeS3Client())

    with pytest.raises(ValueError, match="storage key"):
        storage.save(key, BytesIO(b"x"))
    with pytest.raises(ValueError, match="storage key"):
        storage.open(key)
    with pytest.raises(ValueError, match="storage key"):
        storage.read(key)
    with pytest.raises(ValueError, match="storage key"):
        storage.delete(key)
    with pytest.raises(ValueError, match="storage key"):
        storage.exists(key)


def test_storage_dependency_builds_s3_storage_for_s3_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_BUCKET", "lumina")
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://localhost:9000")
    monkeypatch.setenv("S3_ACCESS_KEY_ID", "lumina-access")
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "lumina-secret")
    monkeypatch.setattr(storage_dependencies, "settings", load_settings())

    storage = storage_dependencies._build_storage()

    assert isinstance(storage, S3Storage)
    assert storage.provider == "s3:self-hosted"
