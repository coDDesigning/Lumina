import hashlib
import json
from dataclasses import replace
from io import BytesIO
from uuid import uuid4

import pytest
from sqlalchemy import text

from backend.app.config import settings
from backend.app.models import (
    EMBEDDING_DIMENSIONS,
    ChunkEmbedding,
    Course,
    DocumentChunk,
    UploadedDocument,
)
from storage.base import StorageError, generate_portable_key
from workers import hosted_restore


class FakeS3Storage:
    provider = "s3:restore-test"

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.read_sizes: list[int] = []

    def generate_key(self, course_id, document_uuid, validated_file_type):
        return generate_portable_key(course_id, document_uuid, validated_file_type)

    def open(self, key):
        try:
            content = self.objects[key]
        except KeyError as exc:
            raise StorageError() from exc
        storage = self

        class RecordingStream(BytesIO):
            def read(self, size=-1):
                storage.read_sizes.append(size)
                return super().read(size)

        return RecordingStream(content)

    def iter_chunks(self, key, chunk_size):
        with self.open(key) as stored_file:
            while chunk := stored_file.read(chunk_size):
                yield chunk


@pytest.fixture
def restore_context(session_factory, model_graph):
    heads = hosted_restore._code_heads()
    with session_factory() as session:
        if session.get_bind().dialect.name == "sqlite":
            session.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS alembic_version "
                    "(version_num VARCHAR(32) NOT NULL)"
                )
            )
            existing_heads = set(
                session.scalars(text("SELECT version_num FROM alembic_version")).all()
            )
            for head in heads:
                if head not in existing_heads:
                    session.execute(
                        text(
                            "INSERT INTO alembic_version (version_num) VALUES (:head)"
                        ),
                        {"head": head},
                    )
            session.commit()
    return session_factory, model_graph, FakeS3Storage(), heads


def _seed_document(
    restore_context,
    *,
    course=None,
    status="ready",
    content=b"hosted restore source",
    provider=None,
    canonical_key=True,
    save_object=True,
    expected_size=None,
    expected_digest=None,
    add_chunk=True,
    add_embedding=True,
    embedding=None,
):
    session_factory, model_graph, storage, _heads = restore_context
    course = course or model_graph.course
    document_id = uuid4()
    key = storage.generate_key(course.id, document_id, "txt")
    if not canonical_key:
        key = f"courses/{course.id}/documents/{document_id}/other.txt"
    if save_object:
        storage.objects[key] = content

    with session_factory() as session:
        document = UploadedDocument(
            id=document_id,
            original_file_name="private-name.txt",
            file_type="txt",
            mime_type="text/plain",
            file_size=len(content) if expected_size is None else expected_size,
            file_hash=(
                hashlib.sha256(content).hexdigest()
                if expected_digest is None
                else expected_digest
            ),
            user_id=model_graph.user.id,
            course_id=course.id,
            storage_provider=provider or storage.provider,
            storage_key=key,
            status=status,
        )
        session.add(document)
        session.flush()
        chunk_id = None
        if add_chunk:
            chunk = DocumentChunk(
                document_id=document.id,
                course_id=document.course_id,
                chunk_index=0,
                text="restore integrity material",
            )
            session.add(chunk)
            session.flush()
            chunk_id = chunk.id
            if add_embedding:
                session.add(
                    ChunkEmbedding(
                        chunk_id=chunk.id,
                        document_id=document.id,
                        course_id=document.course_id,
                        chunk_index=chunk.chunk_index,
                        embedding=embedding
                        or [1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1),
                        embedding_provider="test",
                        embedding_model="test",
                        dimensions=EMBEDDING_DIMENSIONS,
                    )
                )
        session.commit()
    return document_id, chunk_id, key


def _verify(restore_context):
    session_factory, _model_graph, storage, heads = restore_context
    return hosted_restore.verify_restore(
        session_factory=session_factory,
        storage=storage,
        code_heads=heads,
    )


@pytest.mark.database_contract
def test_clean_restore_passes_and_streams_objects_in_bounded_chunks(
    restore_context,
) -> None:
    content = b"x" * (hosted_restore.STREAM_CHUNK_BYTES + 17)
    _seed_document(restore_context, content=content)

    report = _verify(restore_context)

    assert report.as_dict()["status"] == "pass"
    assert report.documents_checked == 1
    assert report.documents_excluded == 0
    assert report.objects_checked == 1
    assert report.objects_verified == 1
    assert report.ready_documents_checked == 1
    assert report.chunks_checked == 1
    assert report.embeddings_checked == 1
    assert restore_context[2].read_sizes
    assert set(restore_context[2].read_sizes) == {hosted_restore.STREAM_CHUNK_BYTES}


def test_deletion_pending_documents_are_excluded(restore_context) -> None:
    _session_factory, model_graph, _storage, _heads = restore_context
    _seed_document(
        restore_context,
        status="deleting",
        provider="s3:wrong",
        canonical_key=False,
        save_object=False,
        add_chunk=False,
    )
    with restore_context[0]() as session:
        other_course = session.get(Course, model_graph.other_course.id)
        assert other_course is not None
        other_course.is_deleted = True
        session.commit()
    _seed_document(
        restore_context,
        course=model_graph.other_course,
        provider="s3:wrong",
        canonical_key=False,
        save_object=False,
        add_chunk=False,
    )

    report = _verify(restore_context)

    assert report.as_dict()["status"] == "pass"
    assert report.documents_checked == 0
    assert report.documents_excluded == 2


@pytest.mark.parametrize(
    ("overrides", "failure"),
    [
        ({"provider": "s3:wrong"}, "storage_provider"),
        ({"canonical_key": False}, "storage_key"),
    ],
)
def test_wrong_provider_or_noncanonical_key_fails(
    restore_context,
    overrides,
    failure,
) -> None:
    _seed_document(restore_context, **overrides)

    report = _verify(restore_context)

    assert report.failures[failure] == 1
    assert report.failure_count == 1


def test_missing_current_object_fails(restore_context) -> None:
    _seed_document(restore_context, save_object=False)

    report = _verify(restore_context)

    assert report.failures["object_unavailable"] == 1
    assert report.failure_count == 1


@pytest.mark.parametrize(
    ("overrides", "failure"),
    [
        ({"expected_size": 1}, "object_size"),
        ({"expected_digest": "0" * 64}, "object_digest"),
    ],
)
def test_object_size_or_digest_mismatch_fails(
    restore_context,
    overrides,
    failure,
) -> None:
    _seed_document(restore_context, **overrides)

    report = _verify(restore_context)

    assert report.failures[failure] == 1
    assert report.failure_count == 1


@pytest.mark.parametrize(
    ("overrides", "failure"),
    [
        ({"add_chunk": False}, "ready_without_chunks"),
        ({"add_embedding": False}, "embedding_count"),
    ],
)
def test_ready_document_chunk_and_vector_gaps_fail(
    restore_context,
    overrides,
    failure,
) -> None:
    _seed_document(restore_context, **overrides)

    report = _verify(restore_context)

    assert report.failures[failure] == 1
    assert report.failure_count == 1


def test_embedding_denormalized_values_must_match(restore_context) -> None:
    session_factory = restore_context[0]
    _document_id, chunk_id, _key = _seed_document(restore_context)
    with session_factory() as session:
        if session.get_bind().dialect.name != "sqlite":
            pytest.skip("Corrupt foreign-key fixture is SQLite-only")
        engine = session.get_bind()
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.execute(
            text(
                "UPDATE chunk_embeddings SET chunk_index = 7 WHERE chunk_id = :chunk_id"
            ),
            {"chunk_id": chunk_id},
        )
        connection.commit()
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")

    report = _verify(restore_context)

    assert report.failures["embedding_metadata"] == 1
    assert report.failure_count == 1


def test_embedding_dimensions_must_match_the_stored_vector(restore_context) -> None:
    session_factory = restore_context[0]
    _document_id, chunk_id, _key = _seed_document(restore_context)
    with session_factory() as session:
        if session.get_bind().dialect.name != "sqlite":
            pytest.skip("Corrupt check-constraint fixture is SQLite-only")
        engine = session.get_bind()
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            text("UPDATE chunk_embeddings SET dimensions = 7 WHERE chunk_id = :id"),
            {"id": chunk_id},
        )
        connection.commit()
        connection.exec_driver_sql("PRAGMA ignore_check_constraints=OFF")

    report = _verify(restore_context)

    assert report.failures["embedding_vector"] == 1
    assert report.failure_count == 1


def test_zero_embedding_is_not_usable_for_cosine_search(restore_context) -> None:
    _seed_document(
        restore_context,
        embedding=[0.0] * EMBEDDING_DIMENSIONS,
    )

    report = _verify(restore_context)

    assert report.failures["embedding_vector"] == 1
    assert report.failure_count == 1


def test_output_is_aggregate_and_contains_no_sensitive_row_values(
    restore_context,
    capsys,
) -> None:
    document_id, _chunk_id, key = _seed_document(restore_context)
    report = _verify(restore_context)

    hosted_restore._emit(report.as_dict())

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload == report.as_dict()
    assert "private-name.txt" not in output
    assert str(document_id) not in output
    assert key not in output
    assert "hosted restore source" not in output
    assert "postgresql" not in output


def test_schema_heads_must_match_code_heads(restore_context) -> None:
    _seed_document(restore_context)

    report = hosted_restore.verify_restore(
        session_factory=restore_context[0],
        storage=restore_context[2],
        code_heads=["different-code-head"],
    )

    assert report.schema_heads_match is False
    assert report.failures["schema_heads"] == 1


def test_target_url_replaces_only_a_distinct_aws_rds_host() -> None:
    source = (
        "postgresql+psycopg://restore_user:p%40ss@"
        "source.abc123.us-east-1.rds.amazonaws.com:5432/lumina"
        "?sslmode=require&application_name=restore"
    )

    target = hosted_restore.derive_target_database_url(
        source,
        "TARGET.cluster-xyz789.us-east-1.rds.amazonaws.com",
    )

    expected = hosted_restore.make_url(source).set(
        host="target.cluster-xyz789.us-east-1.rds.amazonaws.com"
    )
    assert target == expected


@pytest.mark.parametrize(
    "target_host",
    [
        "localhost",
        "127.0.0.1",
        "https://target.abc.us-east-1.rds.amazonaws.com",
        "target.abc.us-east-1.rds.amazonaws.com:5432",
        "target.rds.amazonaws.com.example.com",
        "target_abc.us-east-1.rds.amazonaws.com",
        " target.abc.us-east-1.rds.amazonaws.com",
        "source.abc123.us-east-1.rds.amazonaws.com",
    ],
)
def test_target_url_rejects_unsafe_or_source_hosts(target_host) -> None:
    source = "postgresql://user:secret@source.abc123.us-east-1.rds.amazonaws.com/db"

    with pytest.raises(hosted_restore.SafetyPreconditionError):
        hosted_restore.derive_target_database_url(source, target_host)


def test_upgrade_runs_alembic_with_only_the_target_database_url(monkeypatch) -> None:
    target = hosted_restore.make_url(
        "postgresql://user:secret@target.abc123.us-east-1.rds.amazonaws.com/db"
    )
    invocation = {}

    class Completed:
        returncode = 0

    def run(*args, **kwargs):
        invocation.update(kwargs)
        invocation["args"] = args
        return Completed()

    monkeypatch.setattr(hosted_restore.subprocess, "run", run)

    hosted_restore._upgrade_target(target)

    assert invocation["env"]["DATABASE_URL"] == target.render_as_string(
        hide_password=False
    )
    assert "DATABASE_URL" not in invocation["args"][0]


@pytest.mark.parametrize(
    ("overrides", "expected_error"),
    [
        ({"deployment_mode": "self_hosted"}, "Hosted deployment"),
        ({"database_url": "sqlite:///lumina.db"}, "PostgreSQL"),
        ({"storage_backend": "local"}, "S3"),
        ({"vector_backend": "chroma"}, "pgvector"),
    ],
)
def test_configuration_rejects_non_hosted_providers(
    monkeypatch,
    overrides,
    expected_error,
) -> None:
    values = {
        "deployment_mode": "hosted",
        "database_url": (
            "postgresql://user:secret@source.abc123.us-east-1.rds.amazonaws.com/db"
        ),
        "storage_backend": "s3",
        "vector_backend": "pgvector",
    }
    values.update(overrides)
    hosted = replace(settings, **values)
    monkeypatch.setattr(hosted_restore, "settings", hosted)

    with pytest.raises(hosted_restore.SafetyPreconditionError, match=expected_error):
        hosted_restore._validate_configuration()


class _DisposableEngine:
    def dispose(self):
        pass


def _main_dependencies(monkeypatch, report):
    source = hosted_restore.make_url(
        "postgresql://user:secret@source.abc123.us-east-1.rds.amazonaws.com/db"
    )
    monkeypatch.setattr(hosted_restore, "_validate_configuration", lambda: source)
    monkeypatch.setattr(hosted_restore, "get_storage", FakeS3Storage)
    monkeypatch.setattr(
        hosted_restore,
        "create_database_engine",
        lambda *args, **kwargs: _DisposableEngine(),
    )
    monkeypatch.setattr(hosted_restore, "sessionmaker", lambda **kwargs: object())
    monkeypatch.setattr(hosted_restore, "verify_restore", lambda **kwargs: report)


def test_main_exit_zero_on_pass(monkeypatch, capsys) -> None:
    _main_dependencies(monkeypatch, hosted_restore.VerificationReport())

    hosted_restore.main(
        [
            "--target-host",
            "target.abc123.us-east-1.rds.amazonaws.com",
            "--verify",
            "--output",
            "json",
        ]
    )

    assert json.loads(capsys.readouterr().out)["status"] == "pass"


def test_main_exit_one_on_integrity_failure(monkeypatch, capsys) -> None:
    report = hosted_restore.VerificationReport()
    report.fail("object_unavailable")
    _main_dependencies(monkeypatch, report)

    with pytest.raises(SystemExit) as exc_info:
        hosted_restore.main(
            [
                "--target-host",
                "target.abc123.us-east-1.rds.amazonaws.com",
                "--verify",
                "--output",
                "json",
            ]
        )

    assert exc_info.value.code == 1
    assert json.loads(capsys.readouterr().out)["status"] == "fail"


def test_main_exit_two_on_safety_failure(monkeypatch, capsys) -> None:
    source = hosted_restore.make_url(
        "postgresql://user:secret@source.abc123.us-east-1.rds.amazonaws.com/db"
    )
    monkeypatch.setattr(hosted_restore, "_validate_configuration", lambda: source)

    with pytest.raises(SystemExit) as exc_info:
        hosted_restore.main(
            [
                "--target-host",
                "not-rds.example.com",
                "--verify",
                "--output",
                "json",
            ]
        )

    assert exc_info.value.code == 2
    output = capsys.readouterr().out
    assert json.loads(output)["status"] == "invalid"
    assert "not-rds.example.com" not in output


def test_invalid_cli_exits_two_with_aggregate_json(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        hosted_restore.main(
            [
                "--target-host",
                "target.abc123.us-east-1.rds.amazonaws.com",
                "--output",
                "json",
            ]
        )

    assert exc_info.value.code == 2
    assert json.loads(capsys.readouterr().out)["status"] == "invalid"
