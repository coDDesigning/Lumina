"""Verified self-hosted backup and restore for SQLite, uploads, and Chroma."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Sequence
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url

from backend.app.config import settings
from backend.app.observability import configure_logging
from storage.base import validate_portable_key

FORMAT_VERSION = 1
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_CONFIG = PROJECT_ROOT / "alembic.ini"
logger = logging.getLogger(__name__)
_CHROMA_VERIFY_SCRIPT = """
import sys
import chromadb

client = chromadb.PersistentClient(path=sys.argv[1])
for listed in client.list_collections():
    collection = listed if hasattr(listed, "count") else client.get_collection(name=str(listed))
    if collection.count() == 0:
        continue
    sample = collection.get(limit=1, include=["embeddings"])
    embeddings = sample.get("embeddings")
    if embeddings is None or len(embeddings) != 1:
        raise RuntimeError("collection cannot return a sample vector")
    collection.query(
        query_embeddings=[embeddings[0]],
        n_results=1,
        include=["metadatas", "distances"],
    )
"""


class BackupError(RuntimeError):
    """Backup archive is unsafe, incomplete, or inconsistent."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _current_heads() -> list[str]:
    return sorted(_script_directory().get_heads())


def _script_directory() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(ALEMBIC_CONFIG)))


def _known_revisions() -> set[str]:
    return {revision.revision for revision in _script_directory().walk_revisions()}


def _verify_database(path: Path, *, expected_heads: list[str]) -> None:
    with closing(sqlite3.connect(path)) as connection:
        quick_check = connection.execute("PRAGMA quick_check").fetchone()
        if quick_check != ("ok",):
            raise BackupError(f"SQLite quick_check failed: {quick_check!r}")
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise BackupError("SQLite foreign_key_check found violations.")
        try:
            heads = sorted(
                row[0]
                for row in connection.execute("SELECT version_num FROM alembic_version")
            )
        except sqlite3.DatabaseError as exc:
            raise BackupError("Backup database has no Alembic revision.") from exc
    if heads != expected_heads:
        raise BackupError(
            f"Backup database heads {heads!r} do not match code heads {expected_heads!r}."
        )


def _copy_tree(source: Path, target: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if not source.exists():
        return entries
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise BackupError(f"Backup source contains a symlink: {path}")
        if not path.is_file():
            continue
        before = path.stat()
        relative = path.relative_to(source)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        after = path.stat()
        checksum = _sha256(destination)
        if (
            before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or checksum != _sha256(path)
        ):
            raise BackupError(f"Backup source changed while being copied: {path}")
        entries.append(
            {
                "path": relative.as_posix(),
                "size": destination.stat().st_size,
                "sha256": checksum,
            }
        )
    return entries


def _path_below(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise BackupError("Backup contains an invalid relative path.")
    path = (root / relative).resolve()
    if path == root.resolve() or not path.is_relative_to(root.resolve()):
        raise BackupError(f"Backup path escapes its root: {relative!r}")
    return path


def _archive_below_root(path: Path, root: Path) -> Path:
    resolved_root = root.resolve()
    resolved = path.resolve()
    if resolved == resolved_root or not resolved.is_relative_to(resolved_root):
        raise BackupError(f"Archive path must stay below {resolved_root}.")
    return resolved


def _upload_references(database_path: Path) -> dict[str, tuple[int, str]]:
    with closing(sqlite3.connect(database_path)) as connection:
        documents = connection.execute(
            """
            SELECT storage_provider, storage_key, file_size, file_hash
            FROM uploaded_documents
            ORDER BY storage_key
            """
        ).fetchall()

    references: dict[str, tuple[int, str]] = {}
    for provider, key, expected_size, expected_hash in documents:
        if provider != "local" and not provider.startswith("local:"):
            raise BackupError(
                f"Self-hosted backup cannot archive storage provider {provider!r}."
            )
        try:
            validate_portable_key(key)
        except (TypeError, ValueError) as exc:
            raise BackupError(f"Referenced upload has an unsafe key: {key!r}") from exc
        references[key] = (expected_size, expected_hash)
    return references


def _copy_referenced_uploads(
    database_path: Path,
    source: Path,
    target: Path,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for key, (expected_size, expected_hash) in _upload_references(
        database_path
    ).items():
        path = _path_below(source, key)
        if path.is_symlink() or not path.is_file():
            raise BackupError(f"Referenced upload is missing or unsafe: {key}")
        destination = _path_below(target, key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        size = destination.stat().st_size
        checksum = _sha256(destination)
        if size != expected_size or checksum != expected_hash:
            raise BackupError(f"Referenced upload failed integrity validation: {key}")
        entries.append({"path": key, "size": size, "sha256": checksum})
    return entries


def _verify_chroma_snapshot(directory: Path) -> None:
    files = [path for path in directory.rglob("*") if path.is_file()]
    if not files:
        return
    database = directory / "chroma.sqlite3"
    if not database.is_file():
        raise BackupError("Chroma snapshot has files but no chroma.sqlite3 database.")
    try:
        with closing(
            sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
        ) as connection:
            quick_check = connection.execute("PRAGMA quick_check").fetchone()
    except sqlite3.DatabaseError as exc:
        raise BackupError("Chroma SQLite integrity check failed.") from exc
    if quick_check != ("ok",):
        raise BackupError(f"Chroma SQLite quick_check failed: {quick_check!r}")
    environment = {
        name: os.environ[name]
        for name in (
            "HOME",
            "LANG",
            "LC_ALL",
            "LD_LIBRARY_PATH",
            "LOCALAPPDATA",
            "PATH",
            "PYTHONPATH",
            "SYSTEMROOT",
            "TEMP",
            "TMP",
            "TMPDIR",
            "USERPROFILE",
            "WINDIR",
        )
        if name in os.environ
    }
    environment["ANONYMIZED_TELEMETRY"] = "FALSE"
    with tempfile.TemporaryDirectory(
        prefix=".lumina-chroma-verify-", dir=directory.parent
    ) as temporary:
        validation_directory = Path(temporary) / "chroma"
        shutil.copytree(directory, validation_directory)
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    _CHROMA_VERIFY_SCRIPT,
                    str(validation_directory),
                ],
                capture_output=True,
                check=False,
                env=environment,
                timeout=300,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise BackupError("Chroma snapshot cannot be opened and queried.") from exc
        if completed.returncode != 0:
            raise BackupError("Chroma snapshot cannot be opened and queried.")
        validation_database = validation_directory / "chroma.sqlite3"
        try:
            with closing(
                sqlite3.connect(
                    f"{validation_database.resolve().as_uri()}?mode=ro", uri=True
                )
            ) as connection:
                quick_check = connection.execute("PRAGMA quick_check").fetchone()
        except sqlite3.DatabaseError as exc:
            raise BackupError("Chroma SQLite integrity check failed.") from exc
        if quick_check != ("ok",):
            raise BackupError(f"Chroma SQLite quick_check failed: {quick_check!r}")


def create_backup(
    archive_path: Path,
    *,
    database_path: Path,
    upload_directory: Path,
    chroma_directory: Path,
    include_chroma_offline: bool,
) -> dict[str, Any]:
    """Create a verified archive; SQLite remains online during its snapshot."""
    archive_path = archive_path.resolve()
    database_path = database_path.resolve()
    upload_directory = upload_directory.resolve()
    chroma_directory = chroma_directory.resolve()
    if archive_path == database_path:
        raise BackupError("Backup archive cannot replace the source database.")
    if not database_path.is_file():
        raise BackupError(f"SQLite database does not exist: {database_path}")
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    heads = _current_heads()

    with tempfile.TemporaryDirectory(
        prefix="lumina-backup-", dir=archive_path.parent
    ) as temporary:
        staging = Path(temporary)
        snapshot = staging / "lumina.db"
        with (
            closing(sqlite3.connect(database_path)) as source,
            closing(sqlite3.connect(snapshot)) as target,
        ):
            source.backup(target)
            target.commit()
        snapshot.chmod(0o600)
        _verify_database(snapshot, expected_heads=heads)

        uploads = _copy_referenced_uploads(
            snapshot, upload_directory, staging / "uploads"
        )
        chroma = (
            _copy_tree(chroma_directory, staging / "chroma")
            if include_chroma_offline
            else []
        )
        if include_chroma_offline:
            _verify_chroma_snapshot(staging / "chroma")
        manifest: dict[str, Any] = {
            "format_version": FORMAT_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "alembic_heads": heads,
            "database": {
                "path": "lumina.db",
                "size": snapshot.stat().st_size,
                "sha256": _sha256(snapshot),
            },
            "uploads": uploads,
            "chroma": chroma,
            "vectors_included": include_chroma_offline,
        }
        manifest_path = staging / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        manifest_path.chmod(0o600)

        temporary_archive = archive_path.with_name(
            f".{archive_path.name}.{uuid4().hex}.tmp"
        )
        try:
            descriptor = os.open(
                temporary_archive,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            os.close(descriptor)
            with tarfile.open(temporary_archive, "w:gz") as archive:
                for name in ("manifest.json", "lumina.db", "uploads", "chroma"):
                    path = staging / name
                    if path.exists():
                        archive.add(path, arcname=name, recursive=True)
            with temporary_archive.open("r+b") as handle:
                os.fsync(handle.fileno())
            os.replace(temporary_archive, archive_path)
            archive_path.chmod(0o600)
            _fsync_directory(archive_path.parent)
        finally:
            temporary_archive.unlink(missing_ok=True)
    return manifest


def _safe_extract(archive_path: Path, destination: Path) -> None:
    root = destination.resolve()
    allowed_roots = {"manifest.json", "lumina.db", "uploads", "chroma"}
    names: set[str] = set()
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive.getmembers():
            normalized = member.name.rstrip("/")
            if not normalized or normalized in names:
                raise BackupError("Backup archive contains a duplicate or empty path.")
            names.add(normalized)
            if normalized.split("/", 1)[0] not in allowed_roots:
                raise BackupError("Backup archive contains an unexpected path.")
            if member.issym() or member.islnk() or member.isdev():
                raise BackupError("Backup archive contains an unsafe link or device.")
            resolved = (root / member.name).resolve()
            if not resolved.is_relative_to(root):
                raise BackupError("Backup archive contains an unsafe path.")
        archive.extractall(root, filter="data")


def _verify_entries(
    root: Path,
    entries: list[dict[str, Any]],
) -> dict[str, tuple[int, str]]:
    if not isinstance(entries, list):
        raise BackupError("Backup manifest file list is invalid.")
    if root.exists() and not root.is_dir():
        raise BackupError("Backup payload root must be a directory.")
    seen: set[str] = set()
    for entry in entries:
        try:
            relative = entry["path"]
            size = entry["size"]
            checksum = entry["sha256"]
        except (KeyError, TypeError) as exc:
            raise BackupError("Backup manifest file entry is invalid.") from exc
        if (
            not isinstance(relative, str)
            or relative in seen
            or type(size) is not int
            or size < 0
        ):
            raise BackupError("Backup manifest file entry is invalid.")
        seen.add(relative)
        path = _path_below(root, relative)
        if not path.is_file():
            raise BackupError(f"Backup member is missing: {relative}")
        if path.stat().st_size != size or _sha256(path) != checksum:
            raise BackupError(f"Backup checksum failed: {relative}")
    actual: set[str] = set()
    if root.exists():
        for path in root.rglob("*"):
            if path.is_symlink():
                raise BackupError("Backup payload contains an unsafe link.")
            if path.is_file():
                actual.add(path.relative_to(root).as_posix())
    if actual != seen:
        raise BackupError("Backup payload membership does not match its manifest.")
    return {entry["path"]: (entry["size"], entry["sha256"]) for entry in entries}


def _require_empty_directory(path: Path) -> None:
    if path.exists() and (path.is_file() or any(path.iterdir())):
        raise BackupError(f"Restore target must be absent or empty: {path}")


def restore_backup(
    archive_path: Path,
    *,
    database_path: Path,
    upload_directory: Path,
    chroma_directory: Path,
) -> dict[str, Any]:
    """Restore a verified archive into an empty, stopped self-hosted stack."""
    database_path = database_path.resolve()
    upload_directory = upload_directory.resolve()
    chroma_directory = chroma_directory.resolve()
    if database_path.exists():
        raise BackupError(f"Restore target must be absent: {database_path}")
    _require_empty_directory(upload_directory)
    _require_empty_directory(chroma_directory)
    upload_directory.parent.mkdir(parents=True, exist_ok=True)
    chroma_directory.parent.mkdir(parents=True, exist_ok=True)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    target_devices = {
        database_path.parent.stat().st_dev,
        upload_directory.parent.stat().st_dev,
        chroma_directory.parent.stat().st_dev,
    }
    if len(target_devices) != 1:
        raise BackupError("Restore targets must share one filesystem.")

    with tempfile.TemporaryDirectory(
        prefix=".lumina-restore-", dir=database_path.parent
    ) as temporary:
        staging = Path(temporary)
        _safe_extract(archive_path, staging)
        try:
            manifest = json.loads(
                (staging / "manifest.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError, TypeError) as exc:
            raise BackupError("Backup manifest is missing or invalid.") from exc
        if manifest.get("format_version") != FORMAT_VERSION:
            raise BackupError("Backup format version is not supported.")
        vectors_included = manifest.get("vectors_included")
        if type(vectors_included) is not bool or (
            not vectors_included and manifest.get("chroma")
        ):
            raise BackupError("Backup manifest vector state is invalid.")

        try:
            database_entry = manifest["database"]
            database = _path_below(staging, database_entry["path"])
            database_size = database_entry["size"]
            database_checksum = database_entry["sha256"]
            manifest_heads = manifest["alembic_heads"]
        except (KeyError, TypeError) as exc:
            raise BackupError("Backup manifest database entry is invalid.") from exc
        if (
            not isinstance(manifest_heads, list)
            or not manifest_heads
            or not all(isinstance(head, str) for head in manifest_heads)
            or not set(manifest_heads).issubset(_known_revisions())
        ):
            raise BackupError("Backup manifest contains an unknown schema revision.")
        if not database.is_file() or (
            database.stat().st_size != database_size
            or _sha256(database) != database_checksum
        ):
            raise BackupError("Backup database checksum failed.")
        _verify_database(database, expected_heads=sorted(manifest_heads))
        upload_entries = _verify_entries(
            staging / "uploads", manifest.get("uploads", [])
        )
        if upload_entries != _upload_references(database):
            raise BackupError("Backup uploads do not match database references.")
        _verify_entries(staging / "chroma", manifest.get("chroma", []))
        if vectors_included:
            _verify_chroma_snapshot(staging / "chroma")

        restored_uploads = staging / "uploads"
        if not restored_uploads.exists():
            restored_uploads.mkdir()
        restored_chroma = staging / "chroma"
        if not vectors_included or not restored_chroma.exists():
            restored_chroma.mkdir(exist_ok=True)
        with database.open("r+b") as handle:
            os.fsync(handle.fileno())
        database.chmod(0o600)
        if upload_directory.exists():
            upload_directory.rmdir()
        if chroma_directory.exists():
            chroma_directory.rmdir()
        os.replace(restored_uploads, upload_directory)
        os.replace(restored_chroma, chroma_directory)
        if database_path.exists():
            raise BackupError(
                f"Restore target appeared during restore: {database_path}"
            )
        os.replace(database, database_path)
        _fsync_directory(database_path.parent)
    return manifest


def _configured_paths() -> tuple[Path, Path, Path]:
    url = make_url(settings.database_url)
    if not settings.is_self_hosted or url.get_backend_name() != "sqlite":
        raise BackupError("Self-hosted backup requires SQLite deployment mode.")
    if (
        url.database in (None, "", ":memory:")
        or str(url.database).startswith("file::memory:")
        or url.query.get("mode") == "memory"
    ):
        raise BackupError("Self-hosted backup requires a file-backed SQLite database.")
    return (
        Path(url.database),
        Path(settings.upload_directory),
        Path(settings.chroma_persist_directory),
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Backup or restore Lumina self-hosted data"
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    backup = subcommands.add_parser("backup")
    backup.add_argument("archive", type=Path)
    backup.add_argument(
        "--archive-root",
        type=Path,
        default=None,
        help="require the archive path to stay below this directory",
    )
    backup.add_argument(
        "--include-chroma-offline",
        action="store_true",
        help="copy Chroma only after API and worker have been stopped",
    )
    restore = subcommands.add_parser("restore")
    restore.add_argument("archive", type=Path)
    restore.add_argument(
        "--archive-root",
        type=Path,
        default=None,
        help="require the archive path to stay below this directory",
    )
    args = parser.parse_args(argv)
    configure_logging(service="maintenance", environment=settings.app_env)
    database, uploads, chroma = _configured_paths()
    archive = (
        _archive_below_root(args.archive, args.archive_root)
        if args.archive_root is not None
        else args.archive
    )
    if args.command == "backup":
        manifest = create_backup(
            archive,
            database_path=database,
            upload_directory=uploads,
            chroma_directory=chroma,
            include_chroma_offline=args.include_chroma_offline,
        )
        logger.info(
            "Self-hosted backup completed",
            extra={
                "event": "self_hosted_backup_completed",
                "uploads": len(manifest["uploads"]),
                "vectors_included": manifest["vectors_included"],
            },
        )
        return
    manifest = restore_backup(
        archive,
        database_path=database,
        upload_directory=uploads,
        chroma_directory=chroma,
    )
    if not manifest.get("vectors_included"):
        logger.warning(
            "Restore completed without vectors; run embedding_backfill before startup",
            extra={"event": "self_hosted_restore_requires_embedding_backfill"},
        )
        return
    logger.info(
        "Self-hosted restore completed",
        extra={"event": "self_hosted_restore_completed", "vectors_included": True},
    )


if __name__ == "__main__":
    main()
