from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.database import get_db
from backend.app.models import User
from schemas.credits import CreditStatusResponse, CreditTransactionResponse
from schemas.prompt_context import EducationLevel
from schemas.response import BaseResponse
from schemas.user import PasswordChangeRequest, UserResponse, UserUpdate
from services.credits import (
    DEFAULT_HISTORY_LIMIT,
    GENERATION_CREDIT_COSTS,
    MAX_HISTORY_LIMIT,
    CreditService,
    next_grant_at,
)
from services.user import UserService
from utils.deps import get_current_user

router = APIRouter(prefix="/api/users", tags=["Users"])


@router.put("/me/model", response_model=BaseResponse[UserResponse])
def update_preferred_model(
    model_name: Annotated[
        str,
        Query(min_length=1, max_length=100, pattern=r"^[^\x00]*$"),
    ],
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Changes the preferred AI model for the user."""
    update_data = UserUpdate(preferred_model=model_name)
    updated_user = UserService.update_user(db, current_user.email, update_data)
    return BaseResponse(
        success=True,
        message=f"Preferred model changed to {model_name}",
        data=updated_user,
    )


@router.put("/me/education-level", response_model=BaseResponse[UserResponse])
def update_education_level(
    education_level: EducationLevel,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Changes the normalized education level recorded for the user."""
    update_data = UserUpdate(education_level=education_level)
    updated_user = UserService.update_user(db, current_user.email, update_data)
    return BaseResponse(
        success=True,
        message=f"Education level changed to {education_level.value}",
        data=updated_user,
    )


@router.put("/me/password", response_model=BaseResponse[None])
def change_my_password(
    payload: PasswordChangeRequest,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Changes the account password after proving the current one.

    The new password is checked by ``utils/password_policy.py``, which is also
    what registration uses, so a password this endpoint accepts is one
    registration would have accepted too.
    """
    UserService.change_password(
        db,
        current_user.id,
        payload.current_password,
        payload.new_password,
    )
    return BaseResponse(
        success=True,
        message="Password changed",
        data=None,
    )


@router.get("/me/credits", response_model=BaseResponse[CreditStatusResponse])
def get_my_credits(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Gets the current credit balance, granting this month's credits if owed.

    Replenishment is evaluated here rather than by a scheduler, so the balance a
    user reads is always the balance the policy says they should have. The
    authenticated snapshot predates that grant, so the balance is re-read.

    The policy travels with the balance so a client can say what an exhausted
    account should do next without hardcoding rules the server owns.
    """
    CreditService.ensure_current_period_grant(db, current_user.id)
    user = db.get(User, current_user.id)
    balance = CreditService.reported_balance(user) if user is not None else None
    metered = balance is not None
    status = CreditStatusResponse(
        credits=balance,
        metering_enabled=CreditService.metering_enabled(),
        email_verification_required=settings.email_verification_required,
        is_email_verified=user is not None and user.email_verified_at is not None,
        monthly_grant=settings.credit_periodic_grant if metered else None,
        balance_cap=settings.credit_max_balance if metered else None,
        next_grant_at=next_grant_at() if metered else None,
        generation_costs=dict(GENERATION_CREDIT_COSTS) if metered else {},
    )
    return BaseResponse(
        success=True,
        message="Credits retrieved",
        data=status,
    )


@router.get(
    "/me/credit-transactions",
    response_model=BaseResponse[list[CreditTransactionResponse]],
)
def list_my_credit_transactions(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=MAX_HISTORY_LIMIT)] = DEFAULT_HISTORY_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """Explains the balance: every change to it, most recent first."""
    transactions = CreditService.list_transactions(
        db, current_user.id, limit=limit, offset=offset
    )
    return BaseResponse(
        success=True,
        message="Credit transactions retrieved",
        data=[CreditTransactionResponse.model_validate(t) for t in transactions],
    )
