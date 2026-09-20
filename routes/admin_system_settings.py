from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from backend.app import settings_overrides
from backend.app.config import settings
from backend.app.database import SessionLocal, get_db
from backend.app.settings_overrides import (
    SettingsRevisionConflict,
    SettingsStoreLocked,
    SettingsStoreUnavailable,
    UnsupportedSettingKey,
)
from schemas.response import BaseResponse
from schemas.system_settings import (
    RestartAccepted,
    RestartRequestBody,
    SystemSettingsInventory,
    SystemSettingsReset,
    SystemSettingsUpdate,
)
from schemas.user import UserResponse
from services import system_settings as settings_service
from services.system_restart import (
    RestartAlreadyRunning,
    RestartCoordinator,
    count_in_flight_work,
    request_restart,
)
from utils.deps import get_current_admin
from utils.exceptions import BadRequestException, ConflictException, NotFoundException

router = APIRouter(prefix="/api/admin/system-settings", tags=["Admin"])
logger = logging.getLogger(__name__)

ADMIN_RESPONSES = {
    401: {"description": "Authentication required"},
    403: {"description": "Administrator privileges required"},
}


def _require_self_hosted() -> None:
    if settings.is_hosted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Configuration is managed by the deployment in hosted mode and "
                "cannot be edited here."
            ),
            headers={"X-Error-Code": "not_available_in_hosted_mode"},
        )


def _store_error(exc: SettingsStoreUnavailable) -> HTTPException:
    logger.error(
        "The configuration override store is unavailable",
        extra={
            "event": "system_settings_store_unavailable",
            "success": False,
        },
    )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=str(exc),
        headers={"X-Error-Code": "settings_store_unavailable"},
    )


def _conflict(exc: SettingsRevisionConflict) -> ConflictException:
    return ConflictException(
        "Another administrator changed configuration while you were editing. "
        "Reload to see the current values.",
        error_code="settings_revision_conflict",
    )


def _validation_failure(errors) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail=[{"key": error.key, "message": error.message} for error in errors],
        headers={"X-Error-Code": "settings_validation_failed"},
    )


def _inventory() -> SystemSettingsInventory:
    return SystemSettingsInventory.model_validate(settings_service.build_inventory())


@router.get(
    "",
    response_model=BaseResponse[SystemSettingsInventory],
    responses=ADMIN_RESPONSES,
)
def read_system_settings(
    current_admin: Annotated[UserResponse, Depends(get_current_admin)],
):
    """Lists every supported configuration key with its source and override state."""
    try:
        inventory = _inventory()
    except SettingsStoreUnavailable as exc:
        raise _store_error(exc) from exc
    return BaseResponse(
        success=True,
        message="System settings retrieved successfully",
        data=inventory,
    )


@router.patch(
    "",
    response_model=BaseResponse[SystemSettingsInventory],
    responses={
        **ADMIN_RESPONSES,
        409: {"description": "The configuration changed while you were editing"},
        422: {"description": "The new configuration is not valid"},
    },
)
def update_system_settings(
    payload: SystemSettingsUpdate,
    current_admin: Annotated[UserResponse, Depends(get_current_admin)],
):
    """Saves administrator overrides as one atomic revision."""
    _require_self_hosted()
    updates, unchanged = settings_service.normalize_updates(payload.values)
    removals = tuple(dict.fromkeys(payload.reset))

    if not updates and not removals:
        raise BadRequestException(
            "No configuration changes were supplied.",
            error_code="bad_request",
        )

    try:
        settings_service.validate_candidate(updates, removals)
    except settings_service.CandidateRejected as exc:
        logger.warning(
            "Administrator submitted an invalid configuration",
            extra={
                "event": "system_settings_rejected",
                "user_id": current_admin.id,
                "item_count": len(updates) + len(removals),
                "settings_keys": sorted(set(updates) | set(removals)),
                "success": False,
            },
        )
        raise _validation_failure(exc.errors) from exc

    try:
        saved = settings_overrides.save_overrides(
            updates,
            expected_revision=payload.expected_revision,
            removals=removals,
        )
    except SettingsRevisionConflict as exc:
        raise _conflict(exc) from exc
    except UnsupportedSettingKey as exc:
        raise BadRequestException(
            f"These settings cannot be overridden here: {', '.join(exc.keys)}",
            error_code="settings_key_not_overridable",
        ) from exc
    except SettingsStoreLocked as exc:
        raise ConflictException(
            str(exc), error_code="settings_revision_conflict"
        ) from exc
    except SettingsStoreUnavailable as exc:
        raise _store_error(exc) from exc

    logger.info(
        "Administrator saved configuration overrides",
        extra={
            "event": "system_settings_saved",
            "user_id": current_admin.id,
            "settings_revision": saved.revision,
            "item_count": len(updates) + len(removals),
            "settings_keys": sorted(set(updates) | set(removals) | set(unchanged)),
            "success": True,
        },
    )
    return BaseResponse(
        success=True,
        message="Configuration saved. Restart Lumina to apply it.",
        data=_inventory(),
    )


@router.delete(
    "/{key}",
    response_model=BaseResponse[SystemSettingsInventory],
    responses={
        **ADMIN_RESPONSES,
        404: {"description": "Unknown configuration key"},
        409: {"description": "The configuration changed while you were editing"},
    },
)
def reset_system_setting(
    key: str,
    expected_revision: int,
    current_admin: Annotated[UserResponse, Depends(get_current_admin)],
):
    """Removes one administrator override so the deployment value applies again."""
    _require_self_hosted()
    from backend.app.settings_registry import SETTINGS_BY_KEY

    if key not in SETTINGS_BY_KEY:
        raise NotFoundException(f"{key} is not a supported configuration key.")

    try:
        settings_service.validate_candidate({}, (key,))
    except settings_service.CandidateRejected as exc:
        raise _validation_failure(exc.errors) from exc

    try:
        saved = settings_overrides.save_overrides(
            {}, expected_revision=expected_revision, removals=(key,)
        )
    except SettingsRevisionConflict as exc:
        raise _conflict(exc) from exc
    except UnsupportedSettingKey as exc:
        raise BadRequestException(
            f"{key} cannot be overridden here.",
            error_code="settings_key_not_overridable",
        ) from exc
    except SettingsStoreUnavailable as exc:
        raise _store_error(exc) from exc

    logger.info(
        "Administrator reset a configuration override",
        extra={
            "event": "system_settings_reset",
            "user_id": current_admin.id,
            "settings_revision": saved.revision,
            "item_count": 1,
            "settings_keys": [key],
            "success": True,
        },
    )
    return BaseResponse(
        success=True,
        message=f"{key} reset. Restart Lumina to apply it.",
        data=_inventory(),
    )


@router.delete(
    "",
    response_model=BaseResponse[SystemSettingsInventory],
    responses={
        **ADMIN_RESPONSES,
        400: {"description": "The reset was not confirmed"},
        409: {"description": "The configuration changed while you were editing"},
    },
)
def reset_all_system_settings(
    payload: SystemSettingsReset,
    current_admin: Annotated[UserResponse, Depends(get_current_admin)],
):
    """Removes every administrator override. The committed .env is not touched."""
    _require_self_hosted()
    if not payload.confirm:
        raise BadRequestException(
            "Resetting every override must be confirmed.",
            error_code="bad_request",
        )

    try:
        settings_service.validate_candidate({}, tuple(_saved_keys()))
    except settings_service.CandidateRejected as exc:
        raise _validation_failure(exc.errors) from exc

    try:
        saved = settings_overrides.reset_all_overrides(
            expected_revision=payload.expected_revision
        )
    except SettingsRevisionConflict as exc:
        raise _conflict(exc) from exc
    except SettingsStoreUnavailable as exc:
        raise _store_error(exc) from exc

    logger.info(
        "Administrator reset every configuration override",
        extra={
            "event": "system_settings_reset_all",
            "user_id": current_admin.id,
            "settings_revision": saved.revision,
            "success": True,
        },
    )
    return BaseResponse(
        success=True,
        message="Every override removed. Restart Lumina to apply it.",
        data=_inventory(),
    )


def _saved_keys() -> list[str]:
    return sorted(settings_overrides.load_overrides().values)


@router.post(
    "/restarts",
    response_model=BaseResponse[RestartAccepted],
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **ADMIN_RESPONSES,
        409: {"description": "A restart is already running, or nothing to apply"},
        503: {"description": "No supervisor is configured to restart Lumina"},
    },
)
def start_restart(
    payload: RestartRequestBody,
    response: Response,
    current_admin: Annotated[UserResponse, Depends(get_current_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """Requests a controlled restart that applies the saved configuration."""
    _require_self_hosted()
    if not settings.supervised_restart:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Nothing would restart this process, so Lumina will not stop itself. "
                "Restart the container yourself to apply saved configuration."
            ),
            headers={"X-Error-Code": "restart_not_supervised"},
        )

    try:
        saved = settings_overrides.load_overrides()
    except SettingsStoreUnavailable as exc:
        raise _store_error(exc) from exc

    if saved.revision != payload.expected_revision:
        raise _conflict(
            SettingsRevisionConflict(payload.expected_revision, saved.revision)
        )
    if saved.revision == settings_overrides.active_revision():
        raise ConflictException(
            "The saved configuration is already active.",
            error_code="restart_nothing_to_apply",
        )

    work = count_in_flight_work(db)
    try:
        request = request_restart(
            target_revision=saved.revision,
            actor_id=current_admin.id,
            changed_keys=tuple(sorted(saved.values)),
            drain_timeout_seconds=settings.system_restart_drain_timeout_seconds,
        )
    except RestartAlreadyRunning as exc:
        raise ConflictException(
            str(exc), error_code="restart_already_in_progress"
        ) from exc
    except SettingsStoreUnavailable as exc:
        raise _store_error(exc) from exc

    logger.info(
        "Administrator requested a restart",
        extra={
            "event": "system_restart_requested",
            "user_id": current_admin.id,
            "settings_revision": saved.revision,
            "item_count": work.total,
            "success": True,
        },
    )

    RestartCoordinator(
        request,
        session_factory=SessionLocal,
        drain_timeout_seconds=settings.system_restart_drain_timeout_seconds,
    ).start()

    response.status_code = status.HTTP_202_ACCEPTED
    return BaseResponse(
        success=True,
        message="Restart requested.",
        data=RestartAccepted(
            request_id=request.request_id,
            target_revision=request.target_revision,
            state=request.state,
            in_flight=work.as_payload(),
        ),
    )


@router.get(
    "/restarts/{request_id}",
    response_model=BaseResponse[SystemSettingsInventory],
    responses={**ADMIN_RESPONSES, 404: {"description": "Unknown restart request"}},
)
def read_restart_status(
    request_id: str,
    current_admin: Annotated[UserResponse, Depends(get_current_admin)],
):
    """Reports where a requested restart has got to."""
    try:
        request = settings_overrides.read_restart_request()
    except SettingsStoreUnavailable as exc:
        raise _store_error(exc) from exc

    if request is None or request.request_id != request_id:
        raise NotFoundException("That restart request is not on record.")

    return BaseResponse(
        success=True,
        message="Restart status retrieved successfully",
        data=_inventory(),
    )
