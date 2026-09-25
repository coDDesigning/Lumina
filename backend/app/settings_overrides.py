from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from backend.app.settings_registry import ALL_KEYS, OVERRIDABLE_KEYS

DEFAULT_STORE_DIRECTORY = "./data/system-settings"
STORE_DIRECTORY_VARIABLE = "SYSTEM_SETTINGS_DIRECTORY"
APPLY_VARIABLE = "LUMINA_SYSTEM_SETTINGS_APPLY"

OVERRIDES_FILENAME = "overrides.json"
LAST_KNOWN_GOOD_FILENAME = "last-known-good.json"
RESTART_FILENAME = "restart.json"
BOOT_FILENAME = "boot.json"
LOCK_FILENAME = ".lock"

BASE_REVISION = 0
MAX_BOOT_ATTEMPTS = 3
STALE_LOCK_SECONDS = 30.0
LOCK_TIMEOUT_SECONDS = 5.0

RESTART_QUEUED = "queued"
RESTART_DRAINING = "draining"
RESTART_RESTARTING = "restarting"
RESTART_READY = "ready"
RESTART_FAILED = "failed"
RESTART_ROLLED_BACK = "rolled_back"

RESTART_TERMINAL_STATES = frozenset(
    {RESTART_READY, RESTART_FAILED, RESTART_ROLLED_BACK}
)
RESTART_ACTIVE_STATES = frozenset(
    {RESTART_QUEUED, RESTART_DRAINING, RESTART_RESTARTING}
)

_state_lock = threading.Lock()
_active_revision = BASE_REVISION
_applied_keys: tuple[str, ...] = ()
_rolled_back_from: int | None = None
_baseline: dict[str, str] | None = None


class SettingsStoreError(RuntimeError):
    pass


class SettingsStoreUnavailable(SettingsStoreError):
    pass


class SettingsStoreLocked(SettingsStoreError):
    pass


class SettingsRevisionConflict(SettingsStoreError):
    def __init__(self, expected: int, actual: int) -> None:
        super().__init__(
            f"Expected configuration revision {expected} but the store holds {actual}."
        )
        self.expected = expected
        self.actual = actual


class UnsupportedSettingKey(SettingsStoreError):
    def __init__(self, keys: tuple[str, ...]) -> None:
        super().__init__(f"Not overridable: {', '.join(keys)}")
        self.keys = keys


@dataclass(frozen=True, slots=True)
class OverrideState:
    revision: int = BASE_REVISION
    values: Mapping[str, str] = field(default_factory=dict)
    updated_at: str | None = None

    def as_payload(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "updated_at": self.updated_at,
            "values": dict(self.values),
        }


@dataclass(frozen=True, slots=True)
class RestartRequest:
    request_id: str
    state: str
    target_revision: int
    requested_at: str
    updated_at: str
    drain_deadline: str | None = None
    detail: str | None = None
    actor_id: int | None = None
    changed_keys: tuple[str, ...] = ()

    def as_payload(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "state": self.state,
            "target_revision": self.target_revision,
            "requested_at": self.requested_at,
            "updated_at": self.updated_at,
            "drain_deadline": self.drain_deadline,
            "detail": self.detail,
            "actor_id": self.actor_id,
            "changed_keys": list(self.changed_keys),
        }

    @property
    def is_active(self) -> bool:
        return self.state in RESTART_ACTIVE_STATES


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def store_directory() -> Path:
    raw = os.getenv(STORE_DIRECTORY_VARIABLE, DEFAULT_STORE_DIRECTORY).strip()
    return Path(raw or DEFAULT_STORE_DIRECTORY)


def _path(name: str) -> Path:
    return store_directory() / name


def _ensure_directory() -> Path:
    directory = store_directory()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
    except OSError as exc:
        raise SettingsStoreUnavailable(
            f"Cannot use the override store at {directory}: {exc}"
        ) from exc
    return directory


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SettingsStoreUnavailable(f"Cannot read {path}: {exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    _ensure_directory()
    temporary = path.with_name(path.name + ".tmp")
    try:
        if temporary.exists():
            temporary.unlink()
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except OSError as exc:
        raise SettingsStoreUnavailable(f"Cannot write {path}: {exc}") from exc


class _FileLock:
    def __init__(self, timeout: float = LOCK_TIMEOUT_SECONDS) -> None:
        self._timeout = timeout
        self._path = _path(LOCK_FILENAME)

    def __enter__(self) -> "_FileLock":
        _ensure_directory()
        deadline = time.monotonic() + self._timeout
        while True:
            try:
                descriptor = os.open(
                    self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
                )
                os.close(descriptor)
                return self
            except FileExistsError:
                if self._clear_if_stale():
                    continue
                if time.monotonic() >= deadline:
                    raise SettingsStoreLocked(
                        "Another administrator is writing configuration right now."
                    ) from None
                time.sleep(0.05)
            except OSError as exc:
                raise SettingsStoreUnavailable(
                    f"Cannot lock the override store: {exc}"
                ) from exc

    def __exit__(self, *_: object) -> None:
        try:
            self._path.unlink()
        except OSError:
            pass

    def _clear_if_stale(self) -> bool:
        try:
            age = time.time() - self._path.stat().st_mtime
        except FileNotFoundError:
            return True
        except OSError:
            return False
        if age <= STALE_LOCK_SECONDS:
            return False
        try:
            self._path.unlink()
        except OSError:
            return False
        return True


def _coerce_state(payload: dict[str, Any] | None) -> OverrideState:
    if not payload:
        return OverrideState()
    revision = payload.get("revision")
    values = payload.get("values")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        return OverrideState()
    if not isinstance(values, dict):
        return OverrideState(revision=revision)
    clean = {
        key: str(value)
        for key, value in values.items()
        if isinstance(key, str) and key in OVERRIDABLE_KEYS and value is not None
    }
    updated_at = payload.get("updated_at")
    return OverrideState(
        revision=revision,
        values=clean,
        updated_at=updated_at if isinstance(updated_at, str) else None,
    )


def load_overrides() -> OverrideState:
    return _coerce_state(_read_json(_path(OVERRIDES_FILENAME)))


def load_last_known_good() -> OverrideState | None:
    payload = _read_json(_path(LAST_KNOWN_GOOD_FILENAME))
    if payload is None:
        return None
    return _coerce_state(payload)


def _write_overrides(state: OverrideState) -> None:
    _write_json(_path(OVERRIDES_FILENAME), state.as_payload())


def _write_last_known_good(state: OverrideState) -> None:
    _write_json(_path(LAST_KNOWN_GOOD_FILENAME), state.as_payload())


def read_restart_request() -> RestartRequest | None:
    payload = _read_json(_path(RESTART_FILENAME))
    if not payload:
        return None
    try:
        return RestartRequest(
            request_id=str(payload["request_id"]),
            state=str(payload["state"]),
            target_revision=int(payload["target_revision"]),
            requested_at=str(payload.get("requested_at") or ""),
            updated_at=str(payload.get("updated_at") or ""),
            drain_deadline=payload.get("drain_deadline"),
            detail=payload.get("detail"),
            actor_id=payload.get("actor_id"),
            changed_keys=tuple(
                str(key)
                for key in payload.get("changed_keys", ())
                if isinstance(key, str)
            ),
        )
    except (KeyError, TypeError, ValueError):
        return None


def write_restart_request(request: RestartRequest) -> RestartRequest:
    _write_json(_path(RESTART_FILENAME), request.as_payload())
    return request


def clear_restart_request() -> None:
    try:
        _path(RESTART_FILENAME).unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise SettingsStoreUnavailable(
            f"Cannot clear the restart request: {exc}"
        ) from exc


def _read_boot_attempts(revision: int) -> int:
    payload = _read_json(_path(BOOT_FILENAME)) or {}
    if payload.get("revision") != revision:
        return 0
    attempts = payload.get("attempts")
    return attempts if isinstance(attempts, int) and attempts > 0 else 0


def _write_boot_attempts(revision: int, attempts: int) -> None:
    _write_json(
        _path(BOOT_FILENAME),
        {"revision": revision, "attempts": attempts, "updated_at": _now()},
    )


def _clear_boot_attempts() -> None:
    try:
        _path(BOOT_FILENAME).unlink()
    except FileNotFoundError:
        return
    except OSError:
        return


def active_revision() -> int:
    with _state_lock:
        return _active_revision


def applied_keys() -> tuple[str, ...]:
    with _state_lock:
        return _applied_keys


def rolled_back_from() -> int | None:
    with _state_lock:
        return _rolled_back_from


def _snapshot_environment() -> dict[str, str]:
    return {key: os.environ[key] for key in ALL_KEYS if key in os.environ}


def baseline_environment() -> dict[str, str]:
    with _state_lock:
        captured = _baseline
    return dict(captured) if captured is not None else _snapshot_environment()


def _apply_to_environment(values: Mapping[str, str]) -> tuple[str, ...]:
    applied = []
    for key, value in sorted(values.items()):
        if key not in OVERRIDABLE_KEYS:
            continue
        os.environ[key] = value
        applied.append(key)
    return tuple(applied)


def _restore_baseline(keys: tuple[str, ...], baseline: Mapping[str, str]) -> None:
    for key in keys:
        if key in baseline:
            os.environ[key] = baseline[key]
        else:
            os.environ.pop(key, None)


def record_boot_attempt() -> int:
    try:
        state = load_overrides()
    except SettingsStoreUnavailable:
        return 0
    if state.revision == BASE_REVISION:
        return 0
    good = load_last_known_good()
    if good is not None and good.revision == state.revision:
        return 0
    try:
        attempts = _read_boot_attempts(state.revision) + 1
        _write_boot_attempts(state.revision, attempts)
    except SettingsStoreUnavailable:
        return 0
    return attempts


def apply_overrides() -> int:
    global _active_revision, _applied_keys, _rolled_back_from, _baseline

    if os.getenv(APPLY_VARIABLE, "1").strip() == "0":
        return BASE_REVISION

    with _state_lock:
        previously_applied = _applied_keys
        baseline = _baseline

    if baseline is not None:
        _restore_baseline(previously_applied, baseline)
    else:
        baseline = _snapshot_environment()

    try:
        state = load_overrides()
    except SettingsStoreUnavailable:
        with _state_lock:
            _baseline = baseline
            _applied_keys = ()
            _active_revision = BASE_REVISION
        return BASE_REVISION

    rollback_from: int | None = None
    if state.revision != BASE_REVISION:
        try:
            state, rollback_from = _roll_back_if_exhausted(state)
        except SettingsStoreUnavailable:
            pass

    applied = _apply_to_environment(state.values)
    with _state_lock:
        _baseline = baseline
        _active_revision = state.revision
        _applied_keys = applied
        _rolled_back_from = rollback_from
    return state.revision


def _roll_back_if_exhausted(state: OverrideState) -> tuple[OverrideState, int | None]:
    good = load_last_known_good()
    if good is not None and good.revision == state.revision:
        return state, None

    if _read_boot_attempts(state.revision) <= MAX_BOOT_ATTEMPTS:
        return state, None

    fallback = good if good is not None else OverrideState()
    failed_revision = state.revision
    _write_overrides(fallback)
    _clear_boot_attempts()
    _mark_rollback(failed_revision, fallback.revision)
    return fallback, failed_revision


def _mark_rollback(failed_revision: int, restored_revision: int) -> None:
    existing = read_restart_request()
    timestamp = _now()
    detail = (
        f"Revision {failed_revision} failed to start "
        f"{MAX_BOOT_ATTEMPTS} times and was rolled back."
    )
    if existing is not None and existing.target_revision == failed_revision:
        request = RestartRequest(
            request_id=existing.request_id,
            state=RESTART_ROLLED_BACK,
            target_revision=restored_revision,
            requested_at=existing.requested_at,
            updated_at=timestamp,
            detail=detail,
            actor_id=existing.actor_id,
            changed_keys=existing.changed_keys,
        )
    else:
        request = RestartRequest(
            request_id=f"rollback-{failed_revision}",
            state=RESTART_ROLLED_BACK,
            target_revision=restored_revision,
            requested_at=timestamp,
            updated_at=timestamp,
            detail=detail,
        )
    write_restart_request(request)


def promote_active_revision() -> bool:
    global _rolled_back_from

    revision = active_revision()
    try:
        good = load_last_known_good()
        if good is not None and good.revision == revision:
            return False
        state = load_overrides()
        if state.revision != revision:
            return False
        _write_last_known_good(state)
        _clear_boot_attempts()
    except SettingsStoreUnavailable:
        return False

    request = read_restart_request()
    if (
        request is not None
        and request.is_active
        and request.target_revision == revision
    ):
        write_restart_request(
            RestartRequest(
                request_id=request.request_id,
                state=RESTART_READY,
                target_revision=revision,
                requested_at=request.requested_at,
                updated_at=_now(),
                detail=None,
                actor_id=request.actor_id,
                changed_keys=request.changed_keys,
            )
        )
    with _state_lock:
        _rolled_back_from = None
    return True


def _reject_unsupported(keys: Mapping[str, Any]) -> None:
    unsupported = tuple(sorted(key for key in keys if key not in OVERRIDABLE_KEYS))
    if unsupported:
        raise UnsupportedSettingKey(unsupported)


def save_overrides(
    updates: Mapping[str, str],
    *,
    expected_revision: int,
    removals: tuple[str, ...] = (),
) -> OverrideState:
    _reject_unsupported(updates)
    _reject_unsupported({key: "" for key in removals})

    with _FileLock():
        current = load_overrides()
        if current.revision != expected_revision:
            raise SettingsRevisionConflict(expected_revision, current.revision)
        values = dict(current.values)
        for key in removals:
            values.pop(key, None)
        for key, value in updates.items():
            values[key] = str(value)
        state = OverrideState(
            revision=current.revision + 1,
            values=values,
            updated_at=_now(),
        )
        _write_overrides(state)
    return state


def reset_all_overrides(*, expected_revision: int) -> OverrideState:
    with _FileLock():
        current = load_overrides()
        if current.revision != expected_revision:
            raise SettingsRevisionConflict(expected_revision, current.revision)
        state = OverrideState(
            revision=current.revision + 1,
            values={},
            updated_at=_now(),
        )
        _write_overrides(state)
    return state


class RevisionWatcher:
    def __init__(
        self,
        on_restart: Callable[[RestartRequest], None],
        *,
        interval_seconds: float = 2.0,
        name: str = "lumina-revision-watcher",
    ) -> None:
        self._on_restart = on_restart
        self._interval = interval_seconds
        self._name = name
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._fired = False

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=self._interval + 1.0)
        self._thread = None

    def check_once(self) -> bool:
        if self._fired:
            return False
        try:
            request = read_restart_request()
        except SettingsStoreUnavailable:
            return False
        if request is None or request.state != RESTART_RESTARTING:
            return False
        if request.target_revision == active_revision():
            return False
        self._fired = True
        self._on_restart(request)
        return True

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            if self.check_once():
                return
