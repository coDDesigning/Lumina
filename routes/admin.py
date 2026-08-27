from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import User
from schemas.ai_usage import AiCostReport
from schemas.course import CourseResponse
from schemas.credits import (
    CreditChangeRequest,
    CreditMutationResponse,
    CreditTransactionResponse,
)
from schemas.response import BaseResponse
from schemas.user import Role, UserResponse, UserUpdate
from services.ai_cost_reporting import build_ai_cost_report
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
