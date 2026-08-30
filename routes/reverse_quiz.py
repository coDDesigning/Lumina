from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import GeneratedOutput
from schemas.response import BaseResponse
from schemas.reverse_quiz import ReverseQuizRequest, ReverseQuizResponse
from schemas.user import UserResponse
from services.reverse_quiz import ReverseQuizService
from services.text_generation import (
    get_text_generation_provider,
    resolve_effective_model,
)
from utils.ai_errors import ai_generation_http_exception
from utils.authorization import AuthorizedCourse, OwnedCourse
from utils.deps import get_current_user
from utils.rate_limit import rate_limit_generation

router = APIRouter(
    prefix="/api/courses",
    tags=["Reverse Quiz"],
)


def _provider_for(preferred_model: str | None):
    effective_model = resolve_effective_model(
        None, preferred_model, required_capability="quiz"
    )
    return get_text_generation_provider(
        effective_model=effective_model,
        require_json_mode=True,
    )


@router.post(
    "/{course_id}/reverse-quiz",
    response_model=BaseResponse[ReverseQuizResponse],
    dependencies=[Depends(rate_limit_generation("reverse_quiz"))],
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"description": "No processed course material is available"},
        401: {"description": "Authentication required"},
        402: {"description": "Insufficient credits"},
        404: {"description": "Course not found"},
        422: {"description": "Invalid request"},
        429: {"description": "AI provider or per-user generation rate limited"},
        503: {"description": "AI provider unreachable"},
        504: {"description": "AI provider timed out"},
    },
)
def generate_reverse_quiz(
    course: OwnedCourse,
    request: ReverseQuizRequest,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    try:
        provider = _provider_for(current_user.preferred_model)

        response = ReverseQuizService.generate(
            db=db,
            course_id=course.id,
            user_id=current_user.id,
            request=request,
            provider=provider,
        )

        db.commit()
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise ai_generation_http_exception(exc, feature="reverse_quiz") from exc

    return BaseResponse(
        success=True,
        message="Reverse quiz generated successfully",
        data=response,
    )


@router.get(
    "/{course_id}/reverse-quizzes",
    response_model=BaseResponse[list[ReverseQuizResponse]],
    responses={
        401: {"description": "Authentication required"},
        404: {"description": "Course not found"},
    },
)
def list_reverse_quizzes(
    course: AuthorizedCourse,
    db: Annotated[Session, Depends(get_db)],
):
    outputs = db.scalars(
        select(GeneratedOutput)
        .where(
            GeneratedOutput.course_id == course.id,
            GeneratedOutput.user_id == course.owner_id,
            GeneratedOutput.output_type == "reverse_quiz",
        )
        .order_by(GeneratedOutput.created_at.desc())
    ).all()

    history = []
    for output in outputs:
        try:
            rq = ReverseQuizResponse.model_validate_json(output.content)
            rq.id = output.id
            history.append(rq)
        except Exception:
            # Skip invalid or corrupted entries
            continue

    return BaseResponse(
        success=True,
        message="Reverse quizzes retrieved successfully",
        data=history,
    )
