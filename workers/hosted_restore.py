"""Read-only integrity verification for an isolated hosted restore target."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import func, or_, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from backend.app.config import (
    STORAGE_BACKEND_S3,
    VECTOR_BACKEND_PGVECTOR,
    settings,
)
from backend.app.database_engine import create_database_engine
from backend.app.models import (
    EMBEDDING_DIMENSIONS,
    ChunkEmbedding,
    Course,
    DocumentChunk,
    UploadedDocument,
)
from backend.app.observability import configure_logging
from storage.base import Storage, generate_portable_key, validate_portable_key
from storage.dependencies import get_storage

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_CONFIG = PROJECT_ROOT / "alembic.ini"
STREAM_CHUNK_BYTES = 1024 * 1024
_RDS_HOST_PATTERN = re.compile(
    r"(?=.{1,253}\Z)"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*"
    r"\.rds\.amazonaws\.com(?:\.cn)?"
)
_FAILURE_KINDS = (
    "database",
    "schema_heads",
    "storage_provider",
    "storage_key",
    "object_unavailable",
    "object_size",
    "object_digest",
    "ready_without_chunks",
    "embedding_count",
    "embedding_metadata",
    "embedding_vector",
    "vector_schema",
)
SessionFactory = Callable[[], Session]


class SafetyPreconditionError(ValueError):
    """The command cannot safely target the requested restore."""


class UpgradeError(RuntimeError):
    """The isolated target could not be upgraded."""


@dataclass
class VerificationReport:
    documents_checked: int = 0
    documents_excluded: int = 0
    objects_checked: int = 0
    objects_verified: int = 0
    ready_documents_checked: int = 0
    chunks_checked: int = 0
    embeddings_checked: int = 0
    schema_heads_match: bool = False
    failures: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(_FAILURE_KINDS, 0)
    )

    def fail(self, kind: str) -> None:
        self.failures[kind] += 1

    @property
    def failure_count(self) -> int:
        return sum(self.failures.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "pass" if self.failure_count == 0 else "fail",
            "documents_checked": self.documents_checked,
            "documents_excluded": self.documents_excluded,
            "objects_checked": self.objects_checked,
            "objects_verified": self.objects_verified,
            "ready_documents_checked": self.ready_documents_checked,
            "chunks_checked": self.chunks_checked,
            "embeddings_checked": self.embeddings_checked,
            "schema_heads_match": self.schema_heads_match,
            "failure_count": self.failure_count,
            "failures": dict(sorted(self.failures.items())),
        }


def _invalid_report() -> dict[str, Any]:
    return {
        "status": "invalid",
        "failure_count": 1,
        "failures": {"safety_precondition": 1},
    }


def _upgrade_failure_report() -> dict[str, Any]:
    return {
        "status": "fail",
        "failure_count": 1,
        "failures": {"database": 1},
    }


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True))


def _validate_configuration() -> URL:
    try:
        source_url = make_url(settings.database_url)
    except (TypeError, ValueError) as exc:
        raise SafetyPreconditionError("Invalid configured database URL.") from exc
    if not settings.is_hosted:
        raise SafetyPreconditionError("Hosted deployment mode is required.")
    if source_url.get_backend_name() != "postgresql" or not source_url.host:
        raise SafetyPreconditionError("Hosted restore requires PostgreSQL.")
    if settings.storage_backend != STORAGE_BACKEND_S3:
        raise SafetyPreconditionError("Hosted restore requires S3 storage.")
    if settings.vector_backend != VECTOR_BACKEND_PGVECTOR:
        raise SafetyPreconditionError("Hosted restore requires pgvector.")
    return source_url


def derive_target_database_url(source_url: str | URL, target_host: str) -> URL:
    """Replace only the source URL host after enforcing the RDS isolation boundary."""
    try:
        parsed_source = make_url(source_url)
    except (TypeError, ValueError) as exc:
        raise SafetyPreconditionError("Invalid configured database URL.") from exc
    if parsed_source.get_backend_name() != "postgresql" or not parsed_source.host:
        raise SafetyPreconditionError("Hosted restore requires PostgreSQL.")
    if not isinstance(target_host, str) or target_host != target_host.strip():
        raise SafetyPreconditionError("Target host must be an AWS RDS hostname.")
    normalized_target = target_host.lower()
    if not _RDS_HOST_PATTERN.fullmatch(normalized_target):
        raise SafetyPreconditionError("Target host must be an AWS RDS hostname.")
    if normalized_target == parsed_source.host.rstrip(".").lower():
        raise SafetyPreconditionError("Restore target must differ from the source.")
    return parsed_source.set(host=normalized_target)


def _code_heads() -> list[str]:
    scripts = ScriptDirectory.from_config(Config(str(ALEMBIC_CONFIG)))
    return sorted(scripts.get_heads())


def _upgrade_target(target_url: URL) -> None:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = target_url.render_as_string(hide_password=False)
    environment["DEPLOYMENT_MODE"] = "hosted"
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                str(ALEMBIC_CONFIG),
                "upgrade",
                "head",
            ],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            check=False,
            timeout=1800,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise UpgradeError("Alembic could not run against the restore target.") from exc
    if completed.returncode != 0:
        raise UpgradeError("Alembic could not upgrade the restore target.")


def _verify_document_object(
    document: Any,
    *,
    storage: Storage,
    report: VerificationReport,
) -> None:
    provider_valid = document.storage_provider == storage.provider
    if not provider_valid:
        report.fail("storage_provider")

    key_valid = True
    try:
        validate_portable_key(document.storage_key)
        canonical_key = generate_portable_key(
            document.course_id,
            document.id,
            document.file_type,
        )
        key_valid = document.storage_key == canonical_key
    except (TypeError, ValueError):
        key_valid = False
    if not key_valid:
        report.fail("storage_key")

    if not provider_valid or not key_valid:
        return

    report.objects_checked += 1
    digest = hashlib.sha256()
    size = 0
    try:
        for chunk in storage.iter_chunks(document.storage_key, STREAM_CHUNK_BYTES):
            size += len(chunk)
            digest.update(chunk)
    except Exception:
        report.fail("object_unavailable")
        return

    if size != document.file_size:
        report.fail("object_size")
    digest_matches = digest.hexdigest() == document.file_hash
    if not digest_matches:
        report.fail("object_digest")
    if size == document.file_size and digest_matches:
        report.objects_verified += 1


def _verify_ready_document(
    session: Session,
    document: Any,
    report: VerificationReport,
) -> None:
    rows = session.execute(
        select(
            DocumentChunk.id,
            DocumentChunk.document_id,
            DocumentChunk.course_id,
            DocumentChunk.chunk_index,
            ChunkEmbedding.id.label("embedding_id"),
            ChunkEmbedding.document_id.label("embedding_document_id"),
            ChunkEmbedding.course_id.label("embedding_course_id"),
            ChunkEmbedding.chunk_index.label("embedding_chunk_index"),
            ChunkEmbedding.dimensions.label("embedding_dimensions"),
            ChunkEmbedding.embedding.label("embedding_vector"),
        )
        .outerjoin(ChunkEmbedding, ChunkEmbedding.chunk_id == DocumentChunk.id)
        .where(DocumentChunk.document_id == document.id)
        .order_by(DocumentChunk.id, ChunkEmbedding.id)
    ).all()
    if not rows:
        report.fail("ready_without_chunks")
        return

    by_chunk: dict[int, list[Any]] = {}
    for row in rows:
        by_chunk.setdefault(row.id, []).append(row)
    report.chunks_checked += len(by_chunk)

    for chunk_rows in by_chunk.values():
        embedding_rows = [row for row in chunk_rows if row.embedding_id is not None]
        report.embeddings_checked += len(embedding_rows)
        if len(embedding_rows) != 1:
            report.fail("embedding_count")
            continue
        row = embedding_rows[0]
        if (
            row.document_id != document.id
            or row.course_id != document.course_id
            or row.embedding_document_id != row.document_id
            or row.embedding_course_id != row.course_id
            or row.embedding_chunk_index != row.chunk_index
        ):
            report.fail("embedding_metadata")
        try:
            vector = list(row.embedding_vector)
            vector_length = len(vector)
            vector_usable = any(value != 0 for value in vector) and all(
                math.isfinite(value) for value in vector
            )
        except (TypeError, ValueError):
            vector_length = 0
            vector_usable = False
        if (
            row.embedding_dimensions != EMBEDDING_DIMENSIONS
            or vector_length != EMBEDDING_DIMENSIONS
            or not vector_usable
        ):
            report.fail("embedding_vector")


def _verify_postgresql_vector_schema(
    session: Session, report: VerificationReport
) -> None:
    extension_present = session.scalar(
        text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
    )
    index_definition = session.scalar(
        text(
            "SELECT pg_get_indexdef(index_state.indexrelid) "
            "FROM pg_index AS index_state "
            "JOIN pg_class AS index_table "
            "ON index_table.oid = index_state.indrelid "
            "JOIN pg_class AS index_relation "
            "ON index_relation.oid = index_state.indexrelid "
            "JOIN pg_namespace AS table_namespace "
            "ON table_namespace.oid = index_table.relnamespace "
            "WHERE table_namespace.nspname = current_schema() "
            "AND index_table.relname = 'chunk_embeddings' "
            "AND index_relation.relname = 'ix_chunk_embeddings_embedding_hnsw' "
            "AND index_state.indisvalid IS TRUE "
            "AND index_state.indisready IS TRUE"
        )
    )
    similarity_probe = session.scalar(
        text(
            "SELECT embedding <=> embedding FROM chunk_embeddings "
            "WHERE (embedding <=> embedding) = 0 LIMIT 1"
        )
    )
    if (
        extension_present is not True
        or not isinstance(index_definition, str)
        or "USING hnsw" not in index_definition
        or "vector_cosine_ops" not in index_definition
        or (similarity_probe is not None and float(similarity_probe) != 0.0)
    ):
        report.fail("vector_schema")


def verify_restore(
    *,
    session_factory: SessionFactory,
    storage: Storage,
    code_heads: Sequence[str] | None = None,
) -> VerificationReport:
    """Verify the target in short read-only transactions without exposing row data."""
    report = VerificationReport()
    try:
        expected_heads = sorted(code_heads) if code_heads is not None else _code_heads()
        with session_factory() as session:
            is_postgresql = session.get_bind().dialect.name == "postgresql"
            if is_postgresql:
                session.execute(text("SET TRANSACTION READ ONLY"))
            database_heads = sorted(
                session.scalars(text("SELECT version_num FROM alembic_version")).all()
            )
            report.schema_heads_match = database_heads == expected_heads
            if not report.schema_heads_match:
                report.fail("schema_heads")
            if is_postgresql:
                _verify_postgresql_vector_schema(session, report)

            excluded = or_(
                UploadedDocument.status == "deleting",
                Course.is_deleted.is_(True),
            )
            report.documents_excluded = (
                session.scalar(
                    select(func.count())
                    .select_from(UploadedDocument)
                    .join(Course, Course.id == UploadedDocument.course_id)
                    .where(excluded)
                )
                or 0
            )
            documents = session.execute(
                select(
                    UploadedDocument.id,
                    UploadedDocument.course_id,
                    UploadedDocument.file_type,
                    UploadedDocument.file_size,
                    UploadedDocument.file_hash,
                    UploadedDocument.storage_provider,
                    UploadedDocument.storage_key,
                    UploadedDocument.status,
                )
                .join(Course, Course.id == UploadedDocument.course_id)
                .where(~excluded)
                .order_by(UploadedDocument.id)
            ).all()
            session.rollback()
            for document in documents:
                report.documents_checked += 1
                _verify_document_object(document, storage=storage, report=report)
                if document.status == "ready":
                    report.ready_documents_checked += 1
                    if is_postgresql:
                        session.execute(text("SET TRANSACTION READ ONLY"))
                    _verify_ready_document(session, document, report)
                    session.rollback()
    except Exception:
        report.fail("database")
    return report


class _AggregateArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        _emit(_invalid_report())
        raise SystemExit(2)


def _build_parser() -> argparse.ArgumentParser:
    parser = _AggregateArgumentParser(
        description="Verify an isolated Lumina hosted restore target."
    )
    parser.add_argument("--target-host", required=True)
    parser.add_argument("--upgrade-schema", action="store_true")
    parser.add_argument("--verify", action="store_true", required=True)
    parser.add_argument("--output", choices=("json",), required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    arguments = _build_parser().parse_args(argv)
    configure_logging(service="maintenance", environment=settings.app_env)

    try:
        source_url = _validate_configuration()
        target_url = derive_target_database_url(source_url, arguments.target_host)
    except SafetyPreconditionError:
        _emit(_invalid_report())
        raise SystemExit(2) from None

    if arguments.upgrade_schema:
        try:
            _upgrade_target(target_url)
        except UpgradeError:
            _emit(_upgrade_failure_report())
            raise SystemExit(1) from None

    engine = None
    try:
        storage = get_storage()
        if not storage.provider.startswith("s3:"):
            raise RuntimeError("Configured storage implementation is not S3.")
        engine = create_database_engine(
            target_url.render_as_string(hide_password=False),
            apply_runtime_timeouts=False,
            create_sqlite_parent_directory=False,
            poolclass=NullPool,
        )
        factory = sessionmaker(
            bind=engine,
            class_=Session,
            expire_on_commit=False,
            autoflush=False,
        )
        report = verify_restore(session_factory=factory, storage=storage)
        payload = report.as_dict()
    except Exception:
        payload = _upgrade_failure_report()
    finally:
        if engine is not None:
            engine.dispose()

    _emit(payload)
    if payload["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
