from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.database import get_db
from schemas.ai_tutor import AiTutorRequest, AiTutorResponse
from schemas.response import BaseResponse
from schemas.user import UserResponse
from services.ai_tutor import (
    AiTutorError,
    AiTutorService,
    NoReadyCourseMaterialError,
)
from services.text_generation import (
    TextGenerationConnectionError,
    TextGenerationError,
    TextGenerationTimeoutError,
    get_text_generation_provider,
)
from utils.deps import get_current_user

router = APIRouter(
    prefix="/api",
    tags=["AI Tutor"],
)


@router.post(
    "/ai-tutor",
    response_model=BaseResponse[AiTutorResponse],
    responses={
        503: {"description": "AI provider unavailable"},
    },
)
def ask_ai_tutor(
    request: AiTutorRequest,
    current_user: Annotated[
        UserResponse,
        Depends(get_current_user),
    ],
    db: Annotated[Session, Depends(get_db)],
):
    try:
        provider = get_text_generation_provider()

        response = AiTutorService.generate(
            db,
            request.course_id,
            request.question,
            provider,
            user_id=current_user.id,
        )

    except NoReadyCourseMaterialError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    except (TextGenerationConnectionError, TextGenerationTimeoutError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    except (TextGenerationError, AiTutorError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    return BaseResponse(
        success=True,
        message="AI tutor response generated successfully",
        data=response,
    )
