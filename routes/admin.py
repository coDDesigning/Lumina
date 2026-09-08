import logging
import csv
import io
import json
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import User
from schemas.ai_usage import AiCostReport
from schemas.admin_logs import (
    AdminLogEventDetail,
    AdminLogList,
    AdminLogSummary,
    AdminLogTrace,
)
from schemas.course import CourseResponse
from schemas.credits import (
    CreditChangeRequest,
    CreditMutationResponse,
    CreditTransactionResponse,
)
from schemas.response import BaseResponse
from schemas.user import Role, UserResponse, UserUpdate
from services.ai_cost_reporting import build_ai_cost_report
from services.admin_logs import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    InvalidLogCursorError,
    InvalidLogQueryError,
    LogFilters,
    LogReadService,
    LogRecordNotFoundError,
    LogSourcesUnavailableError,
    UnsupportedLogFilterError,
    normalize_window,
)
from services.course import CourseService
from services.credits import (
    DEFAULT_HISTORY_LIMIT,
    MAX_HISTORY_LIMIT,
    CreditActor,
    CreditService,
)
from services.user import UserService
from utils.deps import get_current_admin
from utils.exceptions import NotFoundException

router = APIRouter(prefix="/api/admin", tags=["Admin"])
logger = logging.getLogger(__name__)

_LOG_LEVELS = {"INFO", "WARNING", "ERROR", "CRITICAL"}
_LOG_SOURCES = {"operational", "client_report", "ai_telemetry"}


def _log_query_error(exc: Exception) -> HTTPException:
    if isinstance(exc, LogRecordNotFoundError):
        http_status = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, LogSourcesUnavailableError):
        http_status = status.HTTP_503_SERVICE_UNAVAILABLE
    else:
        http_status = status.HTTP_422_UNPROCESSABLE_CONTENT
    return HTTPException(
        status_code=http_status,
        detail=str(exc),
        headers={"X-Error-Code": getattr(exc, "code", "invalid_log_query")},
    )


def _log_filters(
    start: Annotated[datetime | None, Query()] = None,
    end: Annotated[datetime | None, Query()] = None,
    level: Annotated[list[str] | None, Query()] = None,
    source: Annotated[list[str] | None, Query()] = None,
    service: Annotated[list[str] | None, Query()] = None,
    environment: Annotated[list[str] | None, Query()] = None,
    logger_name: Annotated[list[str] | None, Query(alias="logger")] = None,
    event: Annotated[list[str] | None, Query()] = None,
    http_method: Annotated[list[str] | None, Query()] = None,
    http_status: Annotated[list[int] | None, Query()] = None,
    http_status_class: Annotated[list[int] | None, Query()] = None,
    minimum_duration_ms: Annotated[float | None, Query(ge=0)] = None,
    maximum_duration_ms: Annotated[float | None, Query(ge=0)] = None,
    error_code: Annotated[list[str] | None, Query()] = None,
    error_category: Annotated[list[str] | None, Query()] = None,
    error_signature: Annotated[list[str] | None, Query()] = None,
    exception_type: Annotated[list[str] | None, Query()] = None,
    failed_stage: Annotated[list[str] | None, Query()] = None,
    job_type: Annotated[list[str] | None, Query()] = None,
    job_status: Annotated[list[str] | None, Query()] = None,
    attempt_number: Annotated[list[int] | None, Query()] = None,
    request_id: Annotated[str | None, Query(max_length=64)] = None,
    operation_id: Annotated[str | None, Query(max_length=80)] = None,
    job_id: Annotated[int | None, Query(ge=0)] = None,
    user_id: Annotated[int | None, Query(ge=0)] = None,
    course_id: Annotated[int | None, Query(ge=0)] = None,
    document_id: Annotated[str | None, Query(max_length=64)] = None,
    generation_type: Annotated[list[str] | None, Query()] = None,
    provider: Annotated[list[str] | None, Query()] = None,
    model: Annotated[list[str] | None, Query()] = None,
    success: Annotated[bool | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
) -> LogFilters:
    levels = tuple(dict.fromkeys(level or ()))
    sources = tuple(dict.fromkeys(source or ()))
    if not set(levels).issubset(_LOG_LEVELS):
        raise _log_query_error(
            InvalidLogQueryError("One or more requested levels are not collected.")
        )
    if not set(sources).issubset(_LOG_SOURCES):
        raise _log_query_error(
            InvalidLogQueryError("One or more requested log sources are unsupported.")
        )
    if http_status and any(value < 100 or value > 599 for value in http_status):
        raise _log_query_error(
            InvalidLogQueryError("HTTP status codes must be between 100 and 599.")
        )
    if http_status_class and any(value < 1 or value > 5 for value in http_status_class):
        raise _log_query_error(
            InvalidLogQueryError("HTTP status classes must be between 1 and 5.")
        )
    if attempt_number and any(value < 0 for value in attempt_number):
        raise _log_query_error(
            InvalidLogQueryError("Attempt numbers must not be negative.")
        )
    if (
        minimum_duration_ms is not None
        and maximum_duration_ms is not None
        and minimum_duration_ms > maximum_duration_ms
    ):
        raise _log_query_error(
            InvalidLogQueryError("Minimum duration cannot exceed maximum duration.")
        )
    try:
        normalized_start, normalized_end = normalize_window(start, end)
    except InvalidLogQueryError as exc:
        raise _log_query_error(exc) from exc
    return LogFilters(
        start=normalized_start,
        end=normalized_end,
        levels=levels,
        sources=sources,
        services=tuple(dict.fromkeys(service or ())),
        environments=tuple(dict.fromkeys(environment or ())),
        loggers=tuple(dict.fromkeys(logger_name or ())),
        events=tuple(dict.fromkeys(event or ())),
        http_methods=tuple(dict.fromkeys(value.upper() for value in http_method or ())),
        http_statuses=tuple(dict.fromkeys(http_status or ())),
        http_status_classes=tuple(dict.fromkeys(http_status_class or ())),
        minimum_duration_ms=minimum_duration_ms,
        maximum_duration_ms=maximum_duration_ms,
        error_codes=tuple(dict.fromkeys(error_code or ())),
        error_categories=tuple(dict.fromkeys(error_category or ())),
        error_signatures=tuple(dict.fromkeys(error_signature or ())),
        exception_types=tuple(dict.fromkeys(exception_type or ())),
        failed_stages=tuple(dict.fromkeys(failed_stage or ())),
        job_types=tuple(dict.fromkeys(job_type or ())),
        job_statuses=tuple(dict.fromkeys(job_status or ())),
        attempt_numbers=tuple(dict.fromkeys(attempt_number or ())),
        request_id=request_id,
        operation_id=operation_id,
        job_id=job_id,
        user_id=user_id,
        course_id=course_id,
        document_id=document_id,
        generation_types=tuple(dict.fromkeys(generation_type or ())),
        providers=tuple(dict.fromkeys(provider or ())),
        models=tuple(dict.fromkeys(model or ())),
        success=success,
        search=search.strip() if search and search.strip() else None,
    )


def _log_service(db: Session) -> LogReadService:
    return LogReadService(db)


@router.get(
    "/logs",
    response_model=BaseResponse[AdminLogList],
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Administrator privileges required"},
        422: {"description": "The filters or cursor are invalid"},
        503: {"description": "Every selected source is unavailable"},
    },
)
def list_logs(
    current_admin: Annotated[UserResponse, Depends(get_current_admin)],
    db: Annotated[Session, Depends(get_db)],
    filters: Annotated[LogFilters, Depends(_log_filters)],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=2000)] = None,
):
    try:
        result = _log_service(db).list(filters, limit=limit, cursor=cursor)
    except (
        InvalidLogCursorError,
        InvalidLogQueryError,
        UnsupportedLogFilterError,
        LogSourcesUnavailableError,
    ) as exc:
        raise _log_query_error(exc) from exc
    return BaseResponse(
        success=True, message="Operational records retrieved", data=result
    )


@router.get(
    "/logs/summary",
    response_model=BaseResponse[AdminLogSummary],
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Administrator privileges required"},
        422: {"description": "The filters are invalid"},
    },
)
def summarize_logs(
    current_admin: Annotated[UserResponse, Depends(get_current_admin)],
    db: Annotated[Session, Depends(get_db)],
    filters: Annotated[LogFilters, Depends(_log_filters)],
):
    try:
        result = _log_service(db).summary(filters)
    except (InvalidLogQueryError, UnsupportedLogFilterError) as exc:
        raise _log_query_error(exc) from exc
    return BaseResponse(
        success=True, message="Operational summary retrieved", data=result
    )


@router.get(
    "/logs/events/{event_id:path}",
    response_model=BaseResponse[AdminLogEventDetail],
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Administrator privileges required"},
        404: {"description": "The event is missing or expired"},
    },
)
def get_log_event(
    event_id: Annotated[str, Path(min_length=1, max_length=300)],
    current_admin: Annotated[UserResponse, Depends(get_current_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    try:
        result = _log_service(db).detail(event_id)
    except LogRecordNotFoundError as exc:
        raise _log_query_error(exc) from exc
    return BaseResponse(
        success=True, message="Operational event retrieved", data=result
    )


@router.get(
    "/logs/trace",
    response_model=BaseResponse[AdminLogTrace],
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Administrator privileges required"},
        404: {"description": "The event is missing or expired"},
    },
)
def trace_log_event(
    event_id: Annotated[str, Query(min_length=1, max_length=300)],
    current_admin: Annotated[UserResponse, Depends(get_current_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    try:
        result = _log_service(db).trace(event_id)
    except LogRecordNotFoundError as exc:
        raise _log_query_error(exc) from exc
    return BaseResponse(success=True, message="Operation trace retrieved", data=result)


def _csv_cell(value: object) -> str:
    if value is None:
        return ""
    rendered = (
        json.dumps(value, separators=(",", ":"))
        if isinstance(value, (dict, list))
        else str(value)
    )
    if rendered.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + rendered
    return rendered


@router.get(
    "/logs/export",
    response_class=Response,
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Administrator privileges required"},
        422: {"description": "The filters are invalid"},
        503: {"description": "Every selected source is unavailable"},
    },
)
def export_logs(
    current_admin: Annotated[UserResponse, Depends(get_current_admin)],
    db: Annotated[Session, Depends(get_db)],
    filters: Annotated[LogFilters, Depends(_log_filters)],
    format: Annotated[Literal["jsonl", "csv"], Query()] = "jsonl",
):
    try:
        records, truncated, health = _log_service(db).export(filters)
    except (
        InvalidLogQueryError,
        UnsupportedLogFilterError,
        LogSourcesUnavailableError,
    ) as exc:
        raise _log_query_error(exc) from exc
    metadata = {
        "type": "export_metadata",
        "start": filters.start.isoformat(),
        "end": filters.end.isoformat(),
        "record_limit": 10_000,
        "truncated": truncated,
        "partial": truncated or any(item.status != "available" for item in health),
    }
    if format == "jsonl":
        lines = [json.dumps(metadata, separators=(",", ":"))]
        lines.extend(record.model_dump_json(exclude_none=True) for record in records)
        body = "\n".join(lines) + "\n"
        media_type = "application/x-ndjson"
        filename = "lumina-operational-events.jsonl"
    else:
        output = io.StringIO(newline="")
        fieldnames = [
            "export_truncated",
            "id",
            "timestamp",
            "level",
            "source",
            "service",
            "environment",
            "logger",
            "event",
            "description",
            "error_code",
            "error_category",
            "exception_type",
            "source_location",
            "http_method",
            "http_path",
            "http_status",
            "duration_ms",
            "request_id",
            "operation_id",
            "parent_operation_id",
            "job_id",
            "job_type",
            "job_status",
            "attempt_number",
            "failed_stage",
            "user_id",
            "course_id",
            "document_id",
            "generation_type",
            "provider",
            "model",
            "success",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "estimated_cost_usd",
            "pricing_version",
            "details",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            row = record.model_dump(mode="json")
            writer.writerow(
                {
                    name: _csv_cell(
                        truncated if name == "export_truncated" else row.get(name)
                    )
                    for name in fieldnames
                }
            )
        body = output.getvalue()
        media_type = "text/csv"
        filename = "lumina-operational-events.csv"
    return Response(
        body,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Export-Truncated": str(truncated).lower(),
            "X-Export-Record-Limit": "10000",
        },
    )


@router.get(
    "/ai-costs",
    response_model=BaseResponse[AiCostReport],
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Administrator privileges required"},
        422: {"description": "The reporting period is out of range"},
    },
)
def get_ai_costs(
    current_admin: Annotated[UserResponse, Depends(get_current_admin)],
    db: Annotated[Session, Depends(get_db)],
    days: Annotated[int, Query(ge=1, le=366)] = 30,
):
    """Summarize persisted provider-cost estimates in UTC day buckets."""
    return BaseResponse(
        success=True,
        message="AI cost report retrieved",
        data=build_ai_cost_report(db, days=days),
    )


@router.get(
    "/users",
    response_model=BaseResponse[list[UserResponse]],
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Administrator privileges required"},
    },
)
def list_users(
    current_admin: Annotated[UserResponse, Depends(get_current_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """Lists all registered users (Admin only)."""
    users = UserService.list_users(db)
    return BaseResponse(
        success=True,
        message="Users retrieved successfully",
        data=users,
    )


@router.put(
    "/users/{email:path}/ban",
    response_model=BaseResponse[UserResponse],
    responses={
        400: {
            "description": "The initial administrator and the caller cannot be banned"
        },
        401: {"description": "Authentication required"},
        403: {"description": "Administrator privileges required"},
        404: {"description": "User not found"},
        422: {"description": "The request is not a valid ban instruction"},
    },
)
def ban_user(
    email: str,
    is_banned: bool,
    current_admin: Annotated[UserResponse, Depends(get_current_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """Bans or unbans a user."""
    target_user = UserService.get_user_by_email(db, email)
    if is_banned and target_user is not None and target_user.is_initial_admin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The initial administrator cannot be banned.",
        )
    if is_banned and target_user is not None and target_user.id == current_admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Administrators cannot ban their own account.",
        )
    update_data = UserUpdate(is_banned=is_banned)
    updated_user = UserService.update_user(db, email, update_data)
    action = "banned" if is_banned else "unbanned"
    logger.info(
        "Administrator changed account ban state",
        extra={
            "event": "admin_user_ban_changed",
            "action": action,
            "user_id": current_admin.id,
            "owner_id": updated_user.id,
            "success": True,
        },
    )
    return BaseResponse(
        success=True, message=f"User {action} successfully", data=updated_user
    )


@router.put(
    "/users/{email:path}/role",
    response_model=BaseResponse[UserResponse],
    responses={
        400: {
            "description": "The initial administrator and the caller cannot be demoted"
        },
        401: {"description": "Authentication required"},
        403: {"description": "Administrator privileges required"},
        404: {"description": "User not found"},
        422: {"description": "The request does not name a valid role"},
    },
)
def change_user_role(
    email: str,
    role: Role,
    current_admin: Annotated[UserResponse, Depends(get_current_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """Grants or revokes admin privileges."""
    target_user = UserService.get_user_by_email(db, email)
    if role != Role.ADMIN and target_user is not None and target_user.is_initial_admin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The initial administrator cannot be demoted.",
        )
    if (
        role != Role.ADMIN
        and target_user is not None
        and target_user.id == current_admin.id
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Administrators cannot demote their own account.",
        )
    update_data = UserUpdate(role=role)
    updated_user = UserService.update_user(db, email, update_data)
    logger.info(
        "Administrator changed account role",
        extra={
            "event": "admin_role_changed",
            "action": role.value,
            "user_id": current_admin.id,
            "owner_id": updated_user.id,
            "success": True,
        },
    )
    return BaseResponse(
        success=True, message=f"User role updated to {role}", data=updated_user
    )


def _target_user(db: Session, email: str) -> User:
    user = UserService.get_user_by_email(db, email)
    if user is None:
        raise NotFoundException("User not found")
    return user


@router.post(
    "/users/{email:path}/credits",
    response_model=BaseResponse[CreditMutationResponse],
    responses={
        400: {
            "description": (
                "Credit metering is disabled, the account holds no balance, or "
                "the change would take it below zero"
            )
        },
        401: {"description": "Authentication required"},
        403: {"description": "Administrator privileges required"},
        404: {"description": "User not found"},
        422: {"description": "The change is not a valid administrative operation"},
    },
)
def change_user_credits(
    email: str,
    payload: CreditChangeRequest,
    current_admin: Annotated[UserResponse, Depends(get_current_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """Moves a user's balance in either direction and records who did it and why.

    One endpoint rather than a grant route and an adjust route: the two differed
    only by the reason the URL implied, and a reason the caller states is more
    honest than one inferred from a path. The actor is taken from the
    authenticated administrator, never from the request body, so a manual change
    can always be attributed.

    This is an administrator's fastest way to lift an exhausted account off zero
    without waiting for the next month's grant. It is account-level credit
    administration and confers no authority over the user's courses.
    """
    target_user = _target_user(db, email)
    transaction = CreditService.apply_admin_change(
        db,
        target_user.id,
        payload.delta,
        reason=payload.reason,
        actor=CreditActor.admin(current_admin.id, current_admin.email),
        note=payload.note,
    )
    db.refresh(target_user)
    logger.info(
        "Administrator changed account credits",
        extra={
            "event": "admin_credits_changed",
            "action": payload.reason.value,
            "user_id": current_admin.id,
            "owner_id": target_user.id,
            "success": True,
        },
    )
    return BaseResponse(
        success=True,
        message=f"Credits changed by {payload.delta}",
        data=CreditMutationResponse(
            user=UserService.to_response(target_user),
            transaction=CreditTransactionResponse.model_validate(transaction),
        ),
    )


@router.get(
    "/users/{email:path}/credit-transactions",
    response_model=BaseResponse[list[CreditTransactionResponse]],
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Administrator privileges required"},
        404: {"description": "User not found"},
        422: {"description": "The pagination arguments are out of range"},
    },
)
def list_user_credit_transactions(
    email: str,
    current_admin: Annotated[UserResponse, Depends(get_current_admin)],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=MAX_HISTORY_LIMIT)] = DEFAULT_HISTORY_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """Reads another user's credit history. Reading only, like course history."""
    target_user = _target_user(db, email)
    transactions = CreditService.list_transactions(
        db, target_user.id, limit=limit, offset=offset
    )
    return BaseResponse(
        success=True,
        message="Credit transactions retrieved",
        data=[CreditTransactionResponse.model_validate(t) for t in transactions],
    )


@router.get(
    "/users/{email:path}/courses",
    response_model=BaseResponse[list[CourseResponse]],
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Administrator privileges required"},
        404: {"description": "User not found"},
    },
)
def list_user_courses(
    email: str,
    current_admin: Annotated[UserResponse, Depends(get_current_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """Lists the active courses owned by a specific user for administrative support (Admin only)."""
    target_user = _target_user(db, email)
    courses = CourseService.get_courses_by_user_id(db, target_user.id)
    return BaseResponse(
        success=True,
        message="User courses retrieved successfully",
        data=courses,
    )
