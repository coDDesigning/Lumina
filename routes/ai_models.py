from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import User
from schemas.ai_model import AiModelInfo
from schemas.response import BaseResponse
from schemas.user import UserResponse
from services.text_generation import get_available_models
from utils.deps import get_current_user

router = APIRouter(prefix="/api/models", tags=["AI Models"])


@router.get("", response_model=BaseResponse[list[AiModelInfo]])
def list_available_models(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """List AI models available under the active deployment."""
    user = db.get(User, current_user.id)
    models_data = get_available_models(user=user)
    models = [AiModelInfo(**m) for m in models_data]
    return BaseResponse(
        success=True,
        message="Available models retrieved successfully",
        data=models,
    )
