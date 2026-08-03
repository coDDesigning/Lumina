from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.database import get_db
from schemas.response import BaseResponse
from schemas.user import Role, UserResponse, UserUpdate
from services.user import UserService
from utils.deps import get_current_admin

router = APIRouter(prefix="/api/admin", tags=["Admin"])


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
