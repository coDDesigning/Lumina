from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import User
from schemas.credits import CreditTransactionResponse
from schemas.response import BaseResponse
from schemas.user import UserResponse, UserUpdate
from services.credits import (
    DEFAULT_HISTORY_LIMIT,
    MAX_HISTORY_LIMIT,
    CreditService,
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


@router.get("/me/credits", response_model=BaseResponse[dict])
def get_my_credits(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Gets the current credit balance, granting this month's credits if owed.

    Replenishment is evaluated here rather than by a scheduler, so the balance a
    user reads is always the balance the policy says they should have. The
    authenticated snapshot predates that grant, so the balance is re-read.
    """
    CreditService.ensure_current_period_grant(db, current_user.id)
    user = db.get(User, current_user.id)
    balance = CreditService.reported_balance(user) if user is not None else None
    return BaseResponse(
        success=True,
        message="Credits retrieved",
        data={"credits": balance},
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
