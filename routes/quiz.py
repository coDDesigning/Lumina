from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from backend.app.database import get_db
from schemas.quiz import QuizGenerationResult, QuizRequest
from schemas.quiz_attempt import (
    CourseProgressResponse,
    QuizAttemptRequest,
    QuizAttemptResponse,
)
from schemas.response import BaseResponse
from schemas.user import UserResponse
from services.quiz import QuizGenerationError, QuizService
from services.quiz_attempt import QuizAttemptService
from services.text_generation import (
    TextGenerationError,
    get_text_generation_provider,
    resolve_effective_model,
)
from utils.ai_errors import ai_generation_http_exception
from utils.authorization import AuthorizedCourse, OwnedCourse
from utils.deps import get_current_user

router = APIRouter(
    prefix="/api/courses",
    tags=["Quiz"],
)


@router.post(
    "/{course_id}/quiz",
    response_model=BaseResponse[QuizGenerationResult],
    responses={
        400: {"description": "No processed course material is available"},
        401: {"description": "Authentication required"},
        402: {"description": "Insufficient credits"},
        404: {"description": "Course not found"},
        422: {"description": "Invalid quiz request"},
        429: {"description": "AI provider rate limited"},
        503: {"description": "AI provider unreachable"},
        504: {"description": "AI provider timed out"},
    },
)
def generate_quiz(
    course: OwnedCourse,
    request: QuizRequest,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    try:
        effective_model = resolve_effective_model(
            request.model, current_user.preferred_model
        )
        try:
            provider = get_text_generation_provider(effective_model=effective_model)
        except TypeError:
            provider = get_text_generation_provider()

        generation = QuizService.generate(
            db,
            course.id,
            request,
            provider,
            user_id=current_user.id,
        )

        quiz = QuizService.save_generated_quiz(
            db,
            course.id,
            generation.quiz,
        )

    except (TextGenerationError, QuizGenerationError, Exception) as exc:
        raise ai_generation_http_exception(exc, feature="quiz") from exc

    return BaseResponse(
        success=True,
        message="Quiz generated successfully",
        data=QuizGenerationResult(
            quiz=QuizService.build_quiz_view(quiz),
            context_truncated=generation.material.truncated,
            chunks_used=generation.material.chunks_used,
            chunks_available=generation.material.chunks_available,
        ),
    )


@router.post(
    "/{course_id}/quizzes/{quiz_id}/attempts",
    response_model=BaseResponse[QuizAttemptResponse],
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"description": "The submitted answers are invalid"},
        401: {"description": "Authentication required"},
        404: {"description": "Course or quiz not found"},
        422: {"description": "Invalid attempt request"},
    },
)
def submit_quiz_attempt(
    course: OwnedCourse,
    quiz_id: int,
    request: QuizAttemptRequest,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    attempt = QuizAttemptService.record_attempt(
        db,
        course.id,
        quiz_id,
        request,
        user_id=current_user.id,
    )

    return BaseResponse(
        success=True,
        message="Quiz attempt recorded successfully",
        data=attempt,
    )


@router.get(
    "/{course_id}/progress",
    response_model=BaseResponse[CourseProgressResponse],
    responses={
        401: {"description": "Authentication required"},
        404: {"description": "Course not found"},
    },
)
def get_course_progress(
    course: AuthorizedCourse,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    progress = QuizAttemptService.get_course_progress(
        db,
        course.id,
        user_id=current_user.id,
    )

    return BaseResponse(
        success=True,
        message="Course progress retrieved successfully",
        data=progress,
    )
