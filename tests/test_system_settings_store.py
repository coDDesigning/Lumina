import ast
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from backend.app import settings_overrides
from backend.app.settings_overrides import (
    BASE_REVISION,
    MAX_BOOT_ATTEMPTS,
    RESTART_READY,
    RESTART_RESTARTING,
    RESTART_ROLLED_BACK,
    OverrideState,
    RestartRequest,
    RevisionWatcher,
    SettingsRevisionConflict,
    SettingsStoreLocked,
    UnsupportedSettingKey,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "system-settings"
    monkeypatch.setenv("SYSTEM_SETTINGS_DIRECTORY", str(directory))
    monkeypatch.delenv("LUMINA_SYSTEM_SETTINGS_APPLY", raising=False)
    settings_overrides._active_revision = BASE_REVISION
    settings_overrides._applied_keys = ()
    settings_overrides._rolled_back_from = None
    settings_overrides._baseline = None
    return directory


def _now_state() -> OverrideState:
    return settings_overrides.load_overrides()


def test_an_empty_store_reports_the_base_revision() -> None:
    state = _now_state()

    assert state.revision == BASE_REVISION
    assert dict(state.values) == {}


def test_saving_increments_the_revision_and_persists_values() -> None:
    saved = settings_overrides.save_overrides(
        {"RETRIEVAL_CHUNK_LIMIT": "48"}, expected_revision=BASE_REVISION
    )

    assert saved.revision == BASE_REVISION + 1
    assert dict(saved.values) == {"RETRIEVAL_CHUNK_LIMIT": "48"}
    assert dict(_now_state().values) == {"RETRIEVAL_CHUNK_LIMIT": "48"}


def test_saving_merges_with_existing_overrides() -> None:
    first = settings_overrides.save_overrides(
        {"RETRIEVAL_CHUNK_LIMIT": "48"}, expected_revision=BASE_REVISION
    )
    second = settings_overrides.save_overrides(
        {"OCR_DPI": "150"}, expected_revision=first.revision
    )

    assert dict(second.values) == {"RETRIEVAL_CHUNK_LIMIT": "48", "OCR_DPI": "150"}


def test_removals_delete_only_the_named_override() -> None:
    first = settings_overrides.save_overrides(
        {"RETRIEVAL_CHUNK_LIMIT": "48", "OCR_DPI": "150"},
        expected_revision=BASE_REVISION,
    )
    second = settings_overrides.save_overrides(
        {}, expected_revision=first.revision, removals=("OCR_DPI",)
    )

    assert dict(second.values) == {"RETRIEVAL_CHUNK_LIMIT": "48"}


def test_a_stale_revision_is_refused_and_writes_nothing() -> None:
    settings_overrides.save_overrides(
        {"RETRIEVAL_CHUNK_LIMIT": "48"}, expected_revision=BASE_REVISION
    )

    with pytest.raises(SettingsRevisionConflict) as caught:
        settings_overrides.save_overrides(
            {"OCR_DPI": "150"}, expected_revision=BASE_REVISION
        )

    assert caught.value.expected == BASE_REVISION
    assert caught.value.actual == BASE_REVISION + 1
    assert dict(_now_state().values) == {"RETRIEVAL_CHUNK_LIMIT": "48"}


def test_a_non_overridable_key_is_refused() -> None:
    with pytest.raises(UnsupportedSettingKey) as caught:
        settings_overrides.save_overrides(
            {"DATABASE_URL": "sqlite:///x.db"}, expected_revision=BASE_REVISION
        )

    assert caught.value.keys == ("DATABASE_URL",)
    assert _now_state().revision == BASE_REVISION


def test_an_unknown_key_is_refused() -> None:
    with pytest.raises(UnsupportedSettingKey):
        settings_overrides.save_overrides(
            {"NOT_A_REAL_SETTING": "1"}, expected_revision=BASE_REVISION
        )


def test_reset_all_clears_every_override_and_advances_the_revision() -> None:
    first = settings_overrides.save_overrides(
        {"RETRIEVAL_CHUNK_LIMIT": "48", "OCR_DPI": "150"},
        expected_revision=BASE_REVISION,
    )

    reset = settings_overrides.reset_all_overrides(expected_revision=first.revision)

    assert dict(reset.values) == {}
    assert reset.revision == first.revision + 1


def test_reset_all_honours_the_expected_revision() -> None:
    settings_overrides.save_overrides(
        {"OCR_DPI": "150"}, expected_revision=BASE_REVISION
    )

    with pytest.raises(SettingsRevisionConflict):
        settings_overrides.reset_all_overrides(expected_revision=BASE_REVISION)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_the_store_is_owner_only(isolated_store: Path) -> None:
    settings_overrides.save_overrides(
        {"OCR_DPI": "150"}, expected_revision=BASE_REVISION
    )

    directory_mode = stat.S_IMODE(isolated_store.stat().st_mode)
    file_mode = stat.S_IMODE((isolated_store / "overrides.json").stat().st_mode)

    assert directory_mode == 0o700
    assert file_mode == 0o600


def test_a_held_lock_refuses_a_concurrent_write(isolated_store: Path) -> None:
    isolated_store.mkdir(parents=True, exist_ok=True)
    (isolated_store / ".lock").write_text("held", encoding="utf-8")

    with pytest.raises(SettingsStoreLocked):
        settings_overrides.save_overrides(
            {"OCR_DPI": "150"},
            expected_revision=BASE_REVISION,
        )


def test_a_corrupt_store_reads_as_empty_rather_than_crashing(
    isolated_store: Path,
) -> None:
    isolated_store.mkdir(parents=True, exist_ok=True)
    (isolated_store / "overrides.json").write_text("{not json", encoding="utf-8")

    assert _now_state().revision == BASE_REVISION


def test_a_stored_unknown_key_is_ignored_on_read(isolated_store: Path) -> None:
    isolated_store.mkdir(parents=True, exist_ok=True)
    (isolated_store / "overrides.json").write_text(
        json.dumps(
            {
                "revision": 4,
                "values": {"OCR_DPI": "150", "DATABASE_URL": "sqlite:///evil.db"},
            }
        ),
        encoding="utf-8",
    )

    state = _now_state()

    assert dict(state.values) == {"OCR_DPI": "150"}


def test_apply_overrides_sets_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCR_DPI", raising=False)
    saved = settings_overrides.save_overrides(
        {"OCR_DPI": "150"}, expected_revision=BASE_REVISION
    )

    revision = settings_overrides.apply_overrides()

    assert revision == saved.revision
    assert os.environ["OCR_DPI"] == "150"
    assert settings_overrides.active_revision() == saved.revision
    assert settings_overrides.applied_keys() == ("OCR_DPI",)


def test_an_override_outranks_the_deployment_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OCR_DPI", "300")
    settings_overrides.save_overrides(
        {"OCR_DPI": "150"}, expected_revision=BASE_REVISION
    )

    settings_overrides.apply_overrides()

    assert os.environ["OCR_DPI"] == "150"


def test_resetting_an_override_falls_back_to_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OCR_DPI", "300")
    saved = settings_overrides.save_overrides(
        {"OCR_DPI": "150"}, expected_revision=BASE_REVISION
    )
    settings_overrides.reset_all_overrides(expected_revision=saved.revision)

    monkeypatch.setenv("OCR_DPI", "300")
    settings_overrides.apply_overrides()

    assert os.environ["OCR_DPI"] == "300"


def test_apply_can_be_disabled_for_candidate_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OCR_DPI", "300")
    settings_overrides.save_overrides(
        {"OCR_DPI": "150"}, expected_revision=BASE_REVISION
    )
    monkeypatch.setenv("LUMINA_SYSTEM_SETTINGS_APPLY", "0")

    revision = settings_overrides.apply_overrides()

    assert revision == BASE_REVISION
    assert os.environ["OCR_DPI"] == "300"


def test_promotion_records_the_last_known_good_revision() -> None:
    saved = settings_overrides.save_overrides(
        {"OCR_DPI": "150"}, expected_revision=BASE_REVISION
    )
    settings_overrides.apply_overrides()

    assert settings_overrides.promote_active_revision() is True

    good = settings_overrides.load_last_known_good()
    assert good is not None
    assert good.revision == saved.revision
    assert settings_overrides.promote_active_revision() is False


def test_promotion_marks_a_matching_restart_request_ready() -> None:
    saved = settings_overrides.save_overrides(
        {"OCR_DPI": "150"}, expected_revision=BASE_REVISION
    )
    settings_overrides.write_restart_request(
        RestartRequest(
            request_id="req-1",
            state=RESTART_RESTARTING,
            target_revision=saved.revision,
            requested_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
    )
    settings_overrides.apply_overrides()
    settings_overrides.promote_active_revision()

    request = settings_overrides.read_restart_request()
    assert request is not None
    assert request.state == RESTART_READY


def test_a_revision_that_never_promotes_is_rolled_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good = settings_overrides.save_overrides(
        {"OCR_DPI": "150"}, expected_revision=BASE_REVISION
    )
    settings_overrides.apply_overrides()
    settings_overrides.promote_active_revision()

    bad = settings_overrides.save_overrides(
        {"OCR_DPI": "600"}, expected_revision=good.revision
    )

    for _ in range(MAX_BOOT_ATTEMPTS):
        assert settings_overrides.apply_overrides() == bad.revision

    assert settings_overrides.apply_overrides() == good.revision
    assert os.environ["OCR_DPI"] == "150"
    assert settings_overrides.rolled_back_from() == bad.revision

    request = settings_overrides.read_restart_request()
    assert request is not None
    assert request.state == RESTART_ROLLED_BACK
    assert request.target_revision == good.revision


def test_rollback_without_a_last_known_good_falls_back_to_no_overrides() -> None:
    bad = settings_overrides.save_overrides(
        {"OCR_DPI": "600"}, expected_revision=BASE_REVISION
    )

    for _ in range(MAX_BOOT_ATTEMPTS):
        settings_overrides.apply_overrides()

    assert settings_overrides.apply_overrides() == BASE_REVISION
    assert dict(settings_overrides.load_overrides().values) == {}
    assert settings_overrides.rolled_back_from() == bad.revision


def test_a_promoted_revision_never_counts_boot_attempts() -> None:
    saved = settings_overrides.save_overrides(
        {"OCR_DPI": "150"}, expected_revision=BASE_REVISION
    )
    settings_overrides.apply_overrides()
    settings_overrides.promote_active_revision()

    for _ in range(MAX_BOOT_ATTEMPTS * 3):
        assert settings_overrides.apply_overrides() == saved.revision


def test_the_watcher_fires_only_for_a_different_target_revision() -> None:
    seen: list[RestartRequest] = []
    watcher = RevisionWatcher(seen.append)

    assert watcher.check_once() is False

    settings_overrides.write_restart_request(
        RestartRequest(
            request_id="req-1",
            state=RESTART_RESTARTING,
            target_revision=BASE_REVISION,
            requested_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
    )
    assert watcher.check_once() is False

    settings_overrides.write_restart_request(
        RestartRequest(
            request_id="req-2",
            state=RESTART_RESTARTING,
            target_revision=BASE_REVISION + 5,
            requested_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
    )
    assert watcher.check_once() is True
    assert watcher.check_once() is False
    assert [request.request_id for request in seen] == ["req-2"]


def test_the_watcher_ignores_a_request_that_is_not_restarting() -> None:
    seen: list[RestartRequest] = []
    watcher = RevisionWatcher(seen.append)
    settings_overrides.write_restart_request(
        RestartRequest(
            request_id="req-1",
            state="draining",
            target_revision=BASE_REVISION + 5,
            requested_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
    )

    assert watcher.check_once() is False
    assert seen == []


def test_clearing_a_missing_restart_request_is_not_an_error() -> None:
    settings_overrides.clear_restart_request()

    assert settings_overrides.read_restart_request() is None


def test_a_corrupt_restart_request_reads_as_absent(isolated_store: Path) -> None:
    isolated_store.mkdir(parents=True, exist_ok=True)
    (isolated_store / "restart.json").write_text(
        json.dumps({"state": "restarting"}), encoding="utf-8"
    )

    assert settings_overrides.read_restart_request() is None


def test_database_config_applies_overrides_before_reading_the_environment() -> None:
    source = (PROJECT_ROOT / "backend" / "app" / "database_config.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)

    apply_line = None
    first_getenv_line = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "apply_overrides" and apply_line is None:
                apply_line = node.lineno
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "getenv":
                if first_getenv_line is None or node.lineno < first_getenv_line:
                    first_getenv_line = node.lineno

    assert apply_line is not None, (
        "backend/app/database_config.py must call apply_overrides() so every entry "
        "point applies administrator overrides before Settings is built."
    )
    assert first_getenv_line is not None
    assert apply_line < first_getenv_line, (
        "apply_overrides() must run before database_config.py reads the environment."
    )


def test_apply_overrides_runs_at_module_scope() -> None:
    source = (PROJECT_ROOT / "backend" / "app" / "database_config.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)

    module_level_calls = [
        node.value.func.id
        for node in tree.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
    ]

    assert "apply_overrides" in module_level_calls
