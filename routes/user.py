from fastapi import APIRouter, Depends
from schemas.user import UserResponse, UserUpdate
from schemas.response import BaseResponse
from utils.deps import get_current_user
from services.user import UserService

router = APIRouter(prefix="/api/users", tags=["Users"])

@router.put("/me/model", response_model=BaseResponse[UserResponse])
async def update_preferred_model(
    model_name: str, 
    current_user: UserResponse = Depends(get_current_user)
):
    """Changes the preferred AI model for the user."""
    update_data = UserUpdate(preferred_model=model_name)
    updated_user = UserService.update_user(current_user.email, update_data)
    return BaseResponse(success=True, message=f"Preferred model changed to {model_name}", data=updated_user)

@router.get("/me/credits", response_model=BaseResponse[dict])
async def get_my_credits(
    current_user: UserResponse = Depends(get_current_user)
):
    """Gets current credit balance."""
    return BaseResponse(
        success=True, 
        message="Credits retrieved", 
        data={"credits": current_user.credits}
    )
