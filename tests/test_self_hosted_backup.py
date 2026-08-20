import io
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.orm import sessionmaker

from backend.app.config import settings
from backend.app.database_engine import create_database_engine
from backend.app.models import EMBEDDING_DIMENSIONS, DocumentChunk, UploadedDocument
from services.vector_store import ChromaVectorStore, VectorRecord
from workers.embedding_backfill import run_backfill
from workers.self_hosted_backup import (
    BackupError,
    _archive_below_root,
    create_backup,
    restore_backup,
)

pytestmark = [
    pytest.mark.database_contract,
    pytest.mark.skipif(settings.is_hosted, reason="self-hosted SQLite contract"),
]
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _database_path(session_factory) -> Path:
    with session_factory() as session:
        database = session.get_bind().url.database
    assert database is not None
    return Path(database)


def _create_chroma_database(
    directory: Path,
    *,
    document_id=None,
    course_id: int = 1,
    chunk_id: int = 1,
) -> tuple:
    resolved_document_id = document_id or uuid4()
    vector = [1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1)
    store = ChromaVectorStore(persist_directory=str(directory))
    try:
        store.replace_document_vectors(
            None,
            document_id=resolved_document_id,
            course_id=course_id,
            records=[
                VectorRecord(
                    chunk_id=chunk_id,
                    document_id=resolved_document_id,
                    course_id=course_id,
                    chunk_index=0,
                    embedding=vector,
                )
            ],
            embedding_provider="test",
            embedding_model="test",
        )
    finally:
        store.close()
    return resolved_document_id, vector


def _repack_archive(source: Path, archive: Path) -> None:
    with tarfile.open(archive, "w:gz") as handle:
        for name in ("manifest.json", "lumina.db", "uploads", "chroma"):
            path = source / name
            if path.exists():
                handle.add(path, arcname=name)


def _run_alembic(database: Path, *arguments: str) -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "development",
            "DEPLOYMENT_MODE": "self_hosted",
            "DATABASE_URL": f"sqlite:///{database.as_posix()}",
        }
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(PROJECT_ROOT / "alembic.ini"),
            *arguments,
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        check=True,
    )


def test_backup_and_restore_round_trip_sqlite_uploads_and_chroma(
    session_factory,
    model_graph,
    tmp_path: Path,
) -> None:
    database = _database_path(session_factory)
    uploads = tmp_path / "source-uploads"
    chroma = tmp_path / "source-chroma"
    (uploads / "course" / "document").mkdir(parents=True)
    upload = uploads / "course" / "document" / "notes.txt"
    upload.write_bytes(b"study notes")
    document_id = uuid4()
    with session_factory() as session:
        document = UploadedDocument(
            id=document_id,
            original_file_name="notes.txt",
            file_type="txt",
            mime_type="text/plain",
            file_size=upload.stat().st_size,
            file_hash=hashlib.sha256(upload.read_bytes()).hexdigest(),
            user_id=model_graph.user.id,
            course_id=model_graph.course.id,
            storage_provider="local:self-hosted",
            storage_key="course/document/notes.txt",
            status="ready",
        )
        session.add(document)
        session.flush()
        chunk = DocumentChunk(
            document_id=document.id,
            course_id=document.course_id,
            chunk_index=0,
            text="Study notes",
        )
        session.add(chunk)
        session.flush()
        chunk_id = chunk.id
        session.commit()
    _, vector = _create_chroma_database(
        chroma,
        document_id=document_id,
        course_id=model_graph.course.id,
        chunk_id=chunk_id,
    )
    archive = tmp_path / "off-host" / "backup.tar.gz"

    manifest = create_backup(
        archive,
        database_path=database,
        upload_directory=uploads,
        chroma_directory=chroma,
        include_chroma_offline=True,
    )

    restored_database = tmp_path / "restored" / "lumina.db"
    restored_uploads = tmp_path / "restored" / "uploads"
    restored_chroma = tmp_path / "restored" / "chroma"
    restored = restore_backup(
        archive,
        database_path=restored_database,
        upload_directory=restored_uploads,
        chroma_directory=restored_chroma,
    )

    assert manifest["vectors_included"] is True
    assert len(manifest["uploads"]) == 1
    assert restored == manifest
    assert (
        restored_uploads / "course" / "document" / "notes.txt"
    ).read_bytes() == b"study notes"
    with closing(sqlite3.connect(restored_database)) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    restored_store = ChromaVectorStore(persist_directory=str(restored_chroma))
    try:
        results = restored_store.search(
            None,
            course_id=model_graph.course.id,
            query_embedding=vector,
            limit=1,
        )
        assert [result.chunk_id for result in results] == [chunk_id]
    finally:
        restored_store.close()


def test_backup_can_omit_chroma_and_restore_empty_vector_directory(
    session_factory,
    tmp_path: Path,
) -> None:
    database = _database_path(session_factory)
    uploads = tmp_path / "uploads"
    chroma = tmp_path / "chroma"
    uploads.mkdir()
    chroma.mkdir()
    (chroma / "not-backed-up").write_text("vector", encoding="utf-8")
    archive = tmp_path / "backup.tar.gz"

    manifest = create_backup(
        archive,
        database_path=database,
        upload_directory=uploads,
        chroma_directory=chroma,
        include_chroma_offline=False,
    )
    restored_chroma = tmp_path / "new-chroma"
    restore_backup(
        archive,
        database_path=tmp_path / "new.db",
        upload_directory=tmp_path / "new-uploads",
        chroma_directory=restored_chroma,
    )

    assert manifest["vectors_included"] is False
    assert list(restored_chroma.iterdir()) == []


def test_restore_rejects_nonempty_targets(session_factory, tmp_path: Path) -> None:
    database = _database_path(session_factory)
    source_uploads = tmp_path / "source-uploads"
    source_chroma = tmp_path / "source-chroma"
    source_uploads.mkdir()
    source_chroma.mkdir()
    archive = tmp_path / "backup.tar.gz"
    create_backup(
        archive,
        database_path=database,
        upload_directory=source_uploads,
        chroma_directory=source_chroma,
        include_chroma_offline=True,
    )
    target_uploads = tmp_path / "target-uploads"
    target_uploads.mkdir()
    (target_uploads / "existing").write_text("do not overwrite", encoding="utf-8")

    with pytest.raises(BackupError, match="absent or empty"):
        restore_backup(
            archive,
            database_path=tmp_path / "target.db",
            upload_directory=target_uploads,
            chroma_directory=tmp_path / "target-chroma",
        )


@pytest.mark.parametrize("member", ["lumina.db", "chroma/chroma.sqlite3"])
def test_restore_rejects_corrupted_payload(
    session_factory,
    tmp_path: Path,
    member: str,
) -> None:
    database = _database_path(session_factory)
    uploads = tmp_path / "source-uploads"
    chroma = tmp_path / "source-chroma"
    uploads.mkdir()
    _create_chroma_database(chroma)
    archive = tmp_path / "backup.tar.gz"
    create_backup(
        archive,
        database_path=database,
        upload_directory=uploads,
        chroma_directory=chroma,
        include_chroma_offline=True,
    )
    extracted = tmp_path / "extracted"
    with tarfile.open(archive, "r:gz") as handle:
        handle.extractall(extracted, filter="data")
    with (extracted / member).open("ab") as handle:
        handle.write(b"corruption")
    corrupted = tmp_path / "corrupted.tar.gz"
    _repack_archive(extracted, corrupted)

    with pytest.raises(BackupError, match="checksum"):
        restore_backup(
            corrupted,
            database_path=tmp_path / "target.db",
            upload_directory=tmp_path / "target-uploads",
            chroma_directory=tmp_path / "target-chroma",
        )


def test_restore_rejects_logically_corrupt_chroma_store(
    session_factory,
    tmp_path: Path,
) -> None:
    uploads = tmp_path / "source-uploads"
    chroma = tmp_path / "source-chroma"
    uploads.mkdir()
    _create_chroma_database(chroma)
    archive = tmp_path / "backup.tar.gz"
    create_backup(
        archive,
        database_path=_database_path(session_factory),
        upload_directory=uploads,
        chroma_directory=chroma,
        include_chroma_offline=True,
    )
    extracted = tmp_path / "corrupted-chroma"
    with tarfile.open(archive, "r:gz") as handle:
        handle.extractall(extracted, filter="data")
    chroma_database = extracted / "chroma" / "chroma.sqlite3"
    with closing(sqlite3.connect(chroma_database)) as connection:
        connection.execute("DROP TABLE collections")
        connection.commit()
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
    manifest_path = extracted / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    chroma_entry = next(
        entry for entry in manifest["chroma"] if entry["path"] == "chroma.sqlite3"
    )
    chroma_entry["size"] = chroma_database.stat().st_size
    chroma_entry["sha256"] = hashlib.sha256(chroma_database.read_bytes()).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    corrupted = tmp_path / "corrupted-chroma.tar.gz"
    _repack_archive(extracted, corrupted)

    with pytest.raises(BackupError, match="opened and queried"):
        restore_backup(
            corrupted,
            database_path=tmp_path / "target.db",
            upload_directory=tmp_path / "target-uploads",
            chroma_directory=tmp_path / "target-chroma",
        )


def test_restore_rejects_archive_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        member = tarfile.TarInfo("../outside")
        payload = b"unsafe"
        member.size = len(payload)
        handle.addfile(member, io.BytesIO(payload))

    with pytest.raises(BackupError, match="path"):
        restore_backup(
            archive,
            database_path=tmp_path / "target.db",
            upload_directory=tmp_path / "uploads",
            chroma_directory=tmp_path / "chroma",
        )


def test_archive_root_rejects_path_escape(tmp_path: Path) -> None:
    root = tmp_path / "backups"
    root.mkdir()

    with pytest.raises(BackupError, match="stay below"):
        _archive_below_root(root / ".." / "data" / "lumina.db", root)

    assert (
        _archive_below_root(root / "lumina.tar.gz", root)
        == (root / "lumina.tar.gz").resolve()
    )


def test_restore_rejects_file_in_place_of_payload_directory(
    session_factory,
    tmp_path: Path,
) -> None:
    source_uploads = tmp_path / "source-uploads"
    source_chroma = tmp_path / "source-chroma"
    source_uploads.mkdir()
    source_chroma.mkdir()
    archive = tmp_path / "backup.tar.gz"
    create_backup(
        archive,
        database_path=_database_path(session_factory),
        upload_directory=source_uploads,
        chroma_directory=source_chroma,
        include_chroma_offline=False,
    )
    extracted = tmp_path / "malformed"
    with tarfile.open(archive, "r:gz") as handle:
        handle.extractall(extracted, filter="data")
    (extracted / "uploads").write_bytes(b"not a directory")
    malformed = tmp_path / "malformed.tar.gz"
    _repack_archive(extracted, malformed)

    with pytest.raises(BackupError, match="must be a directory"):
        restore_backup(
            malformed,
            database_path=tmp_path / "target.db",
            upload_directory=tmp_path / "target-uploads",
            chroma_directory=tmp_path / "target-chroma",
        )


def test_restore_rejects_corrupted_referenced_upload(
    session_factory,
    model_graph,
    tmp_path: Path,
) -> None:
    document_id = uuid4()
    key = f"courses/{model_graph.course.id}/documents/{document_id}/source.txt"
    uploads = tmp_path / "source-uploads"
    upload = uploads / key
    upload.parent.mkdir(parents=True)
    upload.write_bytes(b"source bytes")
    with session_factory() as session:
        session.add(
            UploadedDocument(
                id=document_id,
                original_file_name="source.txt",
                file_type="txt",
                mime_type="text/plain",
                file_size=upload.stat().st_size,
                file_hash=hashlib.sha256(upload.read_bytes()).hexdigest(),
                user_id=model_graph.user.id,
                course_id=model_graph.course.id,
                storage_provider="local:self-hosted",
                storage_key=key,
                status="ready",
            )
        )
        session.commit()
    chroma = tmp_path / "source-chroma"
    chroma.mkdir()
    archive = tmp_path / "backup.tar.gz"
    create_backup(
        archive,
        database_path=_database_path(session_factory),
        upload_directory=uploads,
        chroma_directory=chroma,
        include_chroma_offline=False,
    )
    extracted = tmp_path / "corrupted-upload"
    with tarfile.open(archive, "r:gz") as handle:
        handle.extractall(extracted, filter="data")
    with (extracted / "uploads" / key).open("ab") as handle:
        handle.write(b"corruption")
    corrupted = tmp_path / "corrupted-upload.tar.gz"
    _repack_archive(extracted, corrupted)

    with pytest.raises(BackupError, match="checksum"):
        restore_backup(
            corrupted,
            database_path=tmp_path / "target.db",
            upload_directory=tmp_path / "target-uploads",
            chroma_directory=tmp_path / "target-chroma",
        )


def test_restore_accepts_known_older_revision_then_migrates_to_head(
    session_factory,
    tmp_path: Path,
) -> None:
    uploads = tmp_path / "source-uploads"
    chroma = tmp_path / "source-chroma"
    uploads.mkdir()
    chroma.mkdir()
    archive = tmp_path / "current.tar.gz"
    manifest = create_backup(
        archive,
        database_path=_database_path(session_factory),
        upload_directory=uploads,
        chroma_directory=chroma,
        include_chroma_offline=False,
    )
    head = manifest["alembic_heads"][0]
    scripts = ScriptDirectory.from_config(Config(str(PROJECT_ROOT / "alembic.ini")))
    previous = scripts.get_revision(head).down_revision
    assert isinstance(previous, str)

    extracted = tmp_path / "older"
    with tarfile.open(archive, "r:gz") as handle:
        handle.extractall(extracted, filter="data")
    database = extracted / "lumina.db"
    _run_alembic(database, "downgrade", previous)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    manifest["alembic_heads"] = [previous]
    manifest["database"]["size"] = database.stat().st_size
    manifest["database"]["sha256"] = hashlib.sha256(database.read_bytes()).hexdigest()
    (extracted / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    older_archive = tmp_path / "older.tar.gz"
    _repack_archive(extracted, older_archive)

    restored_database = tmp_path / "restored" / "lumina.db"
    restore_backup(
        older_archive,
        database_path=restored_database,
        upload_directory=tmp_path / "restored" / "uploads",
        chroma_directory=tmp_path / "restored" / "chroma",
    )
    _run_alembic(restored_database, "upgrade", "head")
    with closing(sqlite3.connect(restored_database)) as connection:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == (head,)


def test_backup_rejects_corrupt_chroma_source(
    session_factory,
    tmp_path: Path,
) -> None:
    chroma = tmp_path / "corrupt-chroma"
    chroma.mkdir()
    (chroma / "chroma.sqlite3").write_bytes(b"not a sqlite database")

    with pytest.raises(BackupError, match="Chroma SQLite"):
        create_backup(
            tmp_path / "backup.tar.gz",
            database_path=_database_path(session_factory),
            upload_directory=tmp_path / "uploads",
            chroma_directory=chroma,
            include_chroma_offline=True,
        )


class _ConstantEmbeddingProvider:
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vector = [1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1)
        return [list(vector) for _ in texts]

    def embed_query(self, _text: str) -> list[float]:
        return [1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1)


def test_restore_without_vectors_backfills_embedding_gaps(
    session_factory,
    model_graph,
    tmp_path: Path,
) -> None:
    document_id = uuid4()
    key = f"courses/{model_graph.course.id}/documents/{document_id}/source.txt"
    uploads = tmp_path / "source-uploads"
    upload = uploads / key
    upload.parent.mkdir(parents=True)
    upload.write_bytes(b"restored source")
    with session_factory() as session:
        document = UploadedDocument(
            id=document_id,
            original_file_name="source.txt",
            file_type="txt",
            mime_type="text/plain",
            file_size=upload.stat().st_size,
            file_hash=hashlib.sha256(upload.read_bytes()).hexdigest(),
            user_id=model_graph.user.id,
            course_id=model_graph.course.id,
            storage_provider="local:self-hosted",
            storage_key=key,
            status="ready",
        )
        session.add(document)
        session.flush()
        session.add(
            DocumentChunk(
                document_id=document.id,
                course_id=document.course_id,
                chunk_index=0,
                text="Restored material needs an embedding.",
            )
        )
        session.commit()

    source_chroma = tmp_path / "source-chroma"
    source_chroma.mkdir()
    archive = tmp_path / "no-vectors.tar.gz"
    create_backup(
        archive,
        database_path=_database_path(session_factory),
        upload_directory=uploads,
        chroma_directory=source_chroma,
        include_chroma_offline=False,
    )
    restored_database = tmp_path / "restored" / "lumina.db"
    restored_uploads = tmp_path / "restored" / "uploads"
    restored_chroma = tmp_path / "restored" / "chroma"
    restore_backup(
        archive,
        database_path=restored_database,
        upload_directory=restored_uploads,
        chroma_directory=restored_chroma,
    )

    engine = create_database_engine(f"sqlite:///{restored_database.as_posix()}")
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    store = ChromaVectorStore(persist_directory=str(restored_chroma))
    try:
        report = run_backfill(
            session_factory=factory,
            vector_store=store,
            embedding_provider=_ConstantEmbeddingProvider(),
            prune_orphans=True,
        )
        with factory() as session:
            assert store.count_document_vectors(session, document_id) == 1
        assert report.vectors_missing == 1
        assert report.vectors_written == 1
    finally:
        store.close()
        engine.dispose()
