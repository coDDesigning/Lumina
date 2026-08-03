from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.app.database import get_db
from schemas.response import BaseResponse
from schemas.user import UserResponse, UserUpdate
from services.user import UserService
from utils.deps import get_current_user

router = APIRouter(prefix="/api/users", tags=["Users"])


@router.put("/me/model", response_model=BaseResponse[UserResponse])
def update_preferred_model(
    model_name: Annotated[str, Query(min_length=1, max_length=100)],
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
):
    """Gets current credit balance."""
    return BaseResponse(
        success=True,
        message="Credits retrieved",
        data={"credits": current_user.credits},
    )
