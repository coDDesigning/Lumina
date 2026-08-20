from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import User
from schemas.credits import (
    CreditAdjustRequest,
    CreditGrantRequest,
    CreditMutationResponse,
    CreditTransactionResponse,
)
from schemas.response import BaseResponse
from schemas.user import Role, UserResponse, UserUpdate
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


@router.get("/users", response_model=BaseResponse[list[UserResponse]])
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


@router.put("/users/{email:path}/ban", response_model=BaseResponse[UserResponse])
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


@router.put("/users/{email:path}/role", response_model=BaseResponse[UserResponse])
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
    "/users/{email:path}/credits/grant",
    response_model=BaseResponse[CreditMutationResponse],
)
def grant_user_credits(
    email: str,
    payload: CreditGrantRequest,
    current_admin: Annotated[UserResponse, Depends(get_current_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """Adds credits to a user's balance and records who granted them.

    The actor is taken from the authenticated administrator, never from the
    request body, so a grant can always be attributed. This is account-level
    credit administration and confers no authority over the user's courses.
    """
    target_user = _target_user(db, email)
    transaction = CreditService.grant(
        db,
        target_user.id,
        payload.amount,
        actor=CreditActor.admin(current_admin.id, current_admin.email),
        note=payload.note,
    )
    db.refresh(target_user)
    return BaseResponse(
        success=True,
        message=f"Granted {payload.amount} credits",
        data=CreditMutationResponse(
            user=UserService.to_response(target_user),
            transaction=CreditTransactionResponse.model_validate(transaction),
        ),
    )


@router.post(
    "/users/{email:path}/credits/adjust",
    response_model=BaseResponse[CreditMutationResponse],
)
def adjust_user_credits(
    email: str,
    payload: CreditAdjustRequest,
    current_admin: Annotated[UserResponse, Depends(get_current_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """Corrects a user's balance in either direction.

    An adjustment that would take the balance below zero is rejected whole:
    neither the balance nor the ledger changes.
    """
    target_user = _target_user(db, email)
    transaction = CreditService.adjust(
        db,
        target_user.id,
        payload.delta,
        actor=CreditActor.admin(current_admin.id, current_admin.email),
        note=payload.note,
    )
    db.refresh(target_user)
    return BaseResponse(
        success=True,
        message=f"Adjusted credits by {payload.delta}",
        data=CreditMutationResponse(
            user=UserService.to_response(target_user),
            transaction=CreditTransactionResponse.model_validate(transaction),
        ),
    )


@router.get(
    "/users/{email:path}/credit-transactions",
    response_model=BaseResponse[list[CreditTransactionResponse]],
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
