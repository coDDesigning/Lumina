"""Regression tests for deploy_local.py integrity guardrails (P2-006, P2-030, P2-032)."""

from __future__ import annotations

import io
import json
import subprocess
import tarfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

import deploy_local
from deploy_local import (
    API_FAMILY,
    API_SERVICE,
    HOSTED_RESTORE_FAMILY,
    WORKER_FAMILY,
    WORKER_SERVICE,
    DeployError,
    check_branch,
    git_head,
    main,
    package_frontend_archive,
    publish_frontend,
    roll_services,
    warn_if_not_main,
    write_state,
)

VALID_SHA = "a" * 40
ALT_SHA = "b" * 40


# ==============================================================================
# P2-006: Clean working tree and branch enforcement
# ==============================================================================


def test_git_head_clean_tree() -> None:
    def fake_run(args, **kwargs):
        if args == ["git", "status", "--porcelain"]:
            return subprocess.CompletedProcess(args, 0, stdout="")
        if args == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(args, 0, stdout=f"{VALID_SHA}\n")
        raise AssertionError(f"unexpected command: {args}")

    with patch("subprocess.run", side_effect=fake_run):
        sha = git_head()
        assert sha == VALID_SHA


def test_git_head_dirty_tree_rejected() -> None:
    def fake_run(args, **kwargs):
        if args == ["git", "status", "--porcelain"]:
            return subprocess.CompletedProcess(
                args, 0, stdout=" M deploy_local.py\n?? untracked.txt\n"
            )
        raise AssertionError(f"unexpected command: {args}")

    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(DeployError, match="working tree is dirty"):
            git_head()


def test_git_head_dirty_tree_allowed_with_flag() -> None:
    def fake_run(args, **kwargs):
        if args == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(args, 0, stdout=f"{VALID_SHA}\n")
        raise AssertionError(f"unexpected command: {args}")

    with patch("subprocess.run", side_effect=fake_run):
        sha = git_head(allow_dirty=True)
        assert sha == VALID_SHA


def test_git_head_invalid_sha_rejected() -> None:
    def fake_run(args, **kwargs):
        if args == ["git", "status", "--porcelain"]:
            return subprocess.CompletedProcess(args, 0, stdout="")
        if args == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(args, 0, stdout="short\n")
        raise AssertionError(f"unexpected command: {args}")

    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(DeployError, match="could not resolve a 40-character commit SHA"):
            git_head()


def test_check_branch_matching_main() -> None:
    def fake_run(args, **kwargs):
        if args == ["git", "rev-parse", "origin/main"]:
            return subprocess.CompletedProcess(args, 0, stdout=f"{VALID_SHA}\n")
        raise AssertionError(f"unexpected command: {args}")

    with patch("subprocess.run", side_effect=fake_run):
        check_branch(VALID_SHA)


def test_check_branch_mismatched_rejected() -> None:
    def fake_run(args, **kwargs):
        if args == ["git", "rev-parse", "origin/main"]:
            return subprocess.CompletedProcess(args, 0, stdout=f"{ALT_SHA}\n")
        raise AssertionError(f"unexpected command: {args}")

    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(DeployError, match="production must be deployed from main"):
            check_branch(VALID_SHA)


def test_check_branch_mismatched_allowed_with_flag(capsys) -> None:
    def fake_run(args, **kwargs):
        if args == ["git", "rev-parse", "origin/main"]:
            return subprocess.CompletedProcess(args, 0, stdout=f"{ALT_SHA}\n")
        raise AssertionError(f"unexpected command: {args}")

    with patch("subprocess.run", side_effect=fake_run):
        check_branch(VALID_SHA, allow_branch=True)
    captured = capsys.readouterr()
    assert "continuing due to --allow-branch" in captured.out


def test_check_branch_missing_origin_main_rejected() -> None:
    def fake_run(args, **kwargs):
        if args == ["git", "rev-parse", "origin/main"]:
            raise subprocess.CalledProcessError(1, args)
        raise AssertionError(f"unexpected command: {args}")

    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(DeployError, match="could not read origin/main"):
            check_branch(VALID_SHA)


def test_check_branch_missing_origin_main_allowed_with_flag(capsys) -> None:
    def fake_run(args, **kwargs):
        if args == ["git", "rev-parse", "origin/main"]:
            raise subprocess.CalledProcessError(1, args)
        raise AssertionError(f"unexpected command: {args}")

    with patch("subprocess.run", side_effect=fake_run):
        check_branch(VALID_SHA, allow_branch=True)
    captured = capsys.readouterr()
    assert "could not read origin/main; continuing due to --allow-branch" in captured.out


def test_warn_if_not_main_alias() -> None:
    assert warn_if_not_main is check_branch


# ==============================================================================
# P2-032: Task Definition Family registration for hosted-restore
# ==============================================================================


def test_roll_services_registers_hosted_restore() -> None:
    mock_ecs = MagicMock()
    mock_waiter = MagicMock()
    mock_ecs.get_waiter.return_value = mock_waiter

    registered: list[tuple[str, str]] = []

    def fake_register(family: str, image: str, *, dry_run: bool) -> str:
        registered.append((family, image))
        return f"arn:aws:ecs:eu-central-1:123456789012:task-definition/{family}:1"

    def fake_current_service(service: str) -> str:
        return f"arn:aws:ecs:eu-central-1:123456789012:task-definition/{service}:1"

    with patch("deploy_local.get_ecs_client", return_value=mock_ecs), patch(
        "deploy_local.register_with_image", side_effect=fake_register
    ), patch("deploy_local.current_service_task_def", side_effect=fake_current_service):
        result = roll_services("123456789012.dkr.ecr.eu-central-1.amazonaws.com/lumina:tag", dry_run=False)

    # 1. Verify register_with_image was invoked for API, Worker, AND hosted-restore families
    families_registered = [fam for fam, _ in registered]
    assert API_FAMILY in families_registered
    assert WORKER_FAMILY in families_registered
    assert HOSTED_RESTORE_FAMILY in families_registered

    # 2. Verify returned dict includes all three
    assert HOSTED_RESTORE_FAMILY in result
    assert API_SERVICE in result
    assert WORKER_SERVICE in result

    # 3. Verify ecs.update_service was ONLY called for API and Worker services, NOT hosted-restore
    updated_services = [call_args.kwargs["service"] for call_args in mock_ecs.update_service.call_args_list]
    assert API_SERVICE in updated_services
    assert WORKER_SERVICE in updated_services
    assert len(updated_services) == 2

    # 4. Verify ECS waiter waited on API and Worker services only
    mock_waiter.wait.assert_called_once_with(
        cluster=deploy_local.CLUSTER,
        services=[API_SERVICE, WORKER_SERVICE],
        WaiterConfig={"Delay": 15, "MaxAttempts": 60},
    )


# ==============================================================================
# P2-030: State persistence on failure and release archive upload
# ==============================================================================


def test_write_state_format(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_file = tmp_path / "deploy_state.json"
    monkeypatch.setattr(deploy_local, "STATE_FILE", state_file)

    previous = {
        API_SERVICE: "arn:aws:ecs:eu-central-1:123456789012:task-definition/lumina-production-api:10",
        WORKER_SERVICE: "arn:aws:ecs:eu-central-1:123456789012:task-definition/lumina-production-worker:10",
    }
    new = {
        API_SERVICE: "arn:aws:ecs:eu-central-1:123456789012:task-definition/lumina-production-api:11",
        WORKER_SERVICE: "arn:aws:ecs:eu-central-1:123456789012:task-definition/lumina-production-worker:11",
        HOSTED_RESTORE_FAMILY: "arn:aws:ecs:eu-central-1:123456789012:task-definition/lumina-production-hosted-restore:11",
    }

    write_state(VALID_SHA, "snap-predeploy-123", previous, new)

    assert state_file.is_file()
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data["release"] == VALID_SHA
    assert data["snapshot_id"] == "snap-predeploy-123"
    assert data["previous_task_definitions"] == previous
    assert data["new_task_definitions"] == new
    assert len(data["rollback"]["services"]) == 2
    assert "snap-predeploy-123" in data["rollback"]["database"]


def test_write_state_none_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_file = tmp_path / "deploy_state.json"
    monkeypatch.setattr(deploy_local, "STATE_FILE", state_file)

    previous = {API_SERVICE: "arn:api:1"}
    write_state(VALID_SHA, None, previous)

    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data["snapshot_id"] is None
    assert data["rollback"]["database"] == "no predeployment snapshot taken"


def test_main_failure_persists_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_file = tmp_path / "deploy_state.json"
    monkeypatch.setattr(deploy_local, "STATE_FILE", state_file)

    monkeypatch.setattr(deploy_local, "require_tools", lambda: None)
    monkeypatch.setattr(deploy_local, "git_head", lambda **kw: VALID_SHA)
    monkeypatch.setattr(deploy_local, "check_branch", lambda rel, **kw: None)
    monkeypatch.setattr(deploy_local, "confirm", lambda rel, **kw: None)
    monkeypatch.setattr(
        deploy_local,
        "current_service_task_def",
        lambda s: f"arn:previous:{s}",
    )
    monkeypatch.setattr(deploy_local, "build_and_push_image", lambda rel, **kw: None)
    monkeypatch.setattr(deploy_local, "build_frontend", lambda **kw: tmp_path / "dist")
    monkeypatch.setattr(deploy_local, "take_snapshot", lambda rel, **kw: "snap-456")

    def fail_migration(image, **kw):
        raise DeployError("alembic upgrade head failed")

    monkeypatch.setattr(deploy_local, "run_migration", fail_migration)

    exit_code = main(["--yes"])
    assert exit_code == 1

    # Verify write_state was executed via finally block!
    assert state_file.is_file()
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data["release"] == VALID_SHA
    assert data["snapshot_id"] == "snap-456"
    assert data["previous_task_definitions"] == {
        API_SERVICE: f"arn:previous:{API_SERVICE}",
        WORKER_SERVICE: f"arn:previous:{WORKER_SERVICE}",
    }
    assert "snap-456" in data["rollback"]["database"]


def test_package_frontend_archive_deterministic(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><html></html>", encoding="utf-8")
    assets_dir = dist / "assets"
    assets_dir.mkdir()
    (assets_dir / "index-abc12345.js").write_text("console.log('test');", encoding="utf-8")
    (assets_dir / "index-def67890.css").write_text("body { margin: 0; }", encoding="utf-8")

    data1, sha1 = package_frontend_archive(dist)
    data2, sha2 = package_frontend_archive(dist)

    # Determinism assertion: identical binary payload and checksum
    assert data1 == data2
    assert sha1 == sha2

    # Verify tar content structure and metadata normalization
    with tarfile.open(fileobj=io.BytesIO(data1), mode="r:gz") as tar:
        members = tar.getmembers()
        names = sorted(m.name for m in members)
        assert names == ["assets/index-abc12345.js", "assets/index-def67890.css", "index.html"]
        for m in members:
            assert m.uid == 0
            assert m.gid == 0
            assert m.uname == ""
            assert m.gname == ""
            assert m.mtime == 0


def test_publish_frontend_uploads_archive(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><script src=\"/assets/app.js\"></script>", encoding="utf-8")
    assets_dir = dist / "assets"
    assets_dir.mkdir()
    (assets_dir / "app.js").write_text("console.log('app');", encoding="utf-8")

    archive_data, expected_sha = package_frontend_archive(dist)

    mock_s3 = MagicMock()
    mock_cf = MagicMock()
    mock_cf.create_invalidation.return_value = {"Invalidation": {"Id": "inv-001"}}
    mock_cf.get_waiter.return_value.wait.return_value = None

    # head_object returns 404 ClientError indicating object does not exist yet
    mock_s3.head_object.side_effect = ClientError(
        {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
    )
    mock_s3.get_paginator.return_value.paginate.return_value = []

    with patch("deploy_local.get_s3_client", return_value=mock_s3), patch(
        "deploy_local.get_cf_client", return_value=mock_cf
    ):
        publish_frontend(dist, release=VALID_SHA, dry_run=False)

    # Verify release archive put_object was executed with exact key, content-type, and sha metadata
    expected_key = f"releases/{VALID_SHA}/frontend.tar.gz"
    put_calls = [
        c for c in mock_s3.put_object.call_args_list if c.kwargs.get("Key") == expected_key
    ]
    assert len(put_calls) == 1
    call_kwargs = put_calls[0].kwargs
    assert call_kwargs["Bucket"] == deploy_local.FRONTEND_BUCKET
    assert call_kwargs["Key"] == expected_key
    assert call_kwargs["ContentType"] == "application/gzip"
    assert call_kwargs["Metadata"] == {"sha256": expected_sha}
    assert call_kwargs["IfNoneMatch"] == "*"


def test_publish_frontend_archive_already_exists_matching_sha(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html>index", encoding="utf-8")

    _, expected_sha = package_frontend_archive(dist)

    mock_s3 = MagicMock()
    mock_cf = MagicMock()
    mock_cf.create_invalidation.return_value = {"Invalidation": {"Id": "inv-001"}}
    mock_s3.get_paginator.return_value.paginate.return_value = []

    # Object already exists with matching sha256
    mock_s3.head_object.return_value = {"Metadata": {"sha256": expected_sha}}

    with patch("deploy_local.get_s3_client", return_value=mock_s3), patch(
        "deploy_local.get_cf_client", return_value=mock_cf
    ):
        publish_frontend(dist, release=VALID_SHA, dry_run=False)

    # put_object should NOT be called for the release archive
    expected_key = f"releases/{VALID_SHA}/frontend.tar.gz"
    put_keys = [c.kwargs.get("Key") for c in mock_s3.put_object.call_args_list]
    assert expected_key not in put_keys


def test_publish_frontend_archive_conflict_sha_rejected(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html>index", encoding="utf-8")

    mock_s3 = MagicMock()
    mock_cf = MagicMock()

    # Object exists with a conflicting sha256
    mock_s3.head_object.return_value = {"Metadata": {"sha256": "different_sha_from_earlier_run"}}

    with patch("deploy_local.get_s3_client", return_value=mock_s3), patch(
        "deploy_local.get_cf_client", return_value=mock_cf
    ):
        with pytest.raises(DeployError, match="already has a different frontend archive"):
            publish_frontend(dist, release=VALID_SHA, dry_run=False)


def test_publish_frontend_dry_run(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()

    with patch("deploy_local.get_s3_client") as mock_get_s3:
        publish_frontend(dist, release=VALID_SHA, dry_run=True)
        mock_get_s3.assert_not_called()
