from fastapi import APIRouter, Depends
from schemas.user import UserResponse, UserUpdate, Role
from schemas.response import BaseResponse
from utils.deps import get_current_admin
from services.user import UserService

router = APIRouter(prefix="/api/admin", tags=["Admin"])

@router.put("/users/{email}/ban", response_model=BaseResponse[UserResponse])
async def ban_user(
    email: str, 
    is_banned: bool, 
    current_admin: UserResponse = Depends(get_current_admin)
):
    """Bans or unbans a user."""
    update_data = UserUpdate(is_banned=is_banned)
    updated_user = UserService.update_user(email, update_data)
    action = "banned" if is_banned else "unbanned"
    return BaseResponse(success=True, message=f"User {action} successfully", data=updated_user)

@router.put("/users/{email}/role", response_model=BaseResponse[UserResponse])
async def change_user_role(
    email: str, 
    role: Role, 
    current_admin: UserResponse = Depends(get_current_admin)
):
    """Grants or revokes admin privileges."""
    update_data = UserUpdate(role=role)
    updated_user = UserService.update_user(email, update_data)
    return BaseResponse(success=True, message=f"User role updated to {role}", data=updated_user)

@router.put("/settings/api-key", response_model=BaseResponse[dict])
async def update_system_api_key(
    api_key: str, 
    current_admin: UserResponse = Depends(get_current_admin)
):
    """Updates the global system API key. (Mock implementation)"""
    # In a real app, save to DB or secure storage
    return BaseResponse(
        success=True, 
        message="System API Key updated successfully", 
        data={"new_key_preview": api_key[:4] + "***"}
    )
