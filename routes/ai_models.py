from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import User
from schemas.ai_model import AiModelInfo, ModelTestRequest, ModelTestResult
from schemas.response import BaseResponse
from schemas.user import Role, UserResponse
from services.model_check import check_model
from services.text_generation import get_available_models
from utils.deps import get_current_user, get_verified_user
from utils.rate_limit import rate_limit_generation

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


@router.post(
    "/test",
    response_model=BaseResponse[ModelTestResult],
    dependencies=[Depends(rate_limit_generation("model_test"))],
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Email verification required"},
        422: {"description": "Invalid request body"},
        429: {"description": "Per-user generation rate limited"},
    },
)
def test_model(
    request: ModelTestRequest,
    current_user: Annotated[UserResponse, Depends(get_verified_user)],
    db: Annotated[Session, Depends(get_db)],
):
    user = db.get(User, current_user.id)
    result = check_model(
        user=user,
        is_admin=current_user.role == Role.ADMIN,
        model_id=request.model_id,
    )
    return BaseResponse(
        success=True,
        message="Model test completed",
        data=result,
    )
