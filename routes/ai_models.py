from typing import Annotated

from fastapi import APIRouter, Depends

from schemas.ai_model import AiModelInfo
from schemas.response import BaseResponse
from schemas.user import UserResponse
from services.text_generation import get_available_models
from utils.deps import get_current_user

router = APIRouter(prefix="/api/models", tags=["AI Models"])


@router.get("", response_model=BaseResponse[list[AiModelInfo]])
def list_available_models(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
):
    """List AI models available under the active deployment."""
    models_data = get_available_models()
    models = [AiModelInfo(**m) for m in models_data]
    return BaseResponse(
        success=True,
        message="Available models retrieved successfully",
        data=models,
    )
