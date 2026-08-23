from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.database import get_db
from schemas.quiz import (
    QuizGenerationContext,
    QuizGenerationResult,
    QuizGenerationSettings,
    QuizRequest,
    QuizSummary,
    QuizView,
)
from schemas.quiz_attempt import (
    CourseProgressResponse,
    QuizAttemptRequest,
    QuizAttemptResponse,
    QuizHistoryItem,
)
from schemas.response import BaseResponse
from schemas.user import UserResponse
from services.generated_output import GeneratedOutputService
from services.quiz import QuizGenerationError, QuizService
from services.quiz_attempt import QuizAttemptService
from services.retrieval_material import RetrievalMaterialError
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


def _provider_for(model: str | None, preferred_model: str | None):
    effective_model = resolve_effective_model(
        model, preferred_model, required_capability="quiz"
    )
    return get_text_generation_provider(
        effective_model=effective_model,
        require_json_mode=True,
    )


@router.post(
    "/{course_id}/quiz",
    response_model=BaseResponse[QuizGenerationResult],
    responses={
        400: {"description": "No processed course material is available"},
        401: {"description": "Authentication required"},
        402: {"description": "Insufficient credits"},
        404: {"description": "Course not found"},
        409: {
            "description": (
                "No course material matched the request, or the course material "
                "is not searchable yet"
            )
        },
        422: {"description": "Invalid quiz request"},
        429: {"description": "AI provider rate limited"},
        503: {"description": "AI provider or course search unreachable"},
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
        provider = _provider_for(request.model, current_user.preferred_model)

        generation = QuizService.generate(
            db,
            course.id,
            request,
            provider,
            user_id=current_user.id,
        )

        applied_settings = QuizGenerationSettings.from_request(
            generation.effective_request,
            retrieval_limit=settings.retrieval_chunk_limit,
            retrieval_min_similarity=settings.retrieval_min_similarity,
        ).model_dump_json()
        applied_context = QuizGenerationContext.from_material(
            generation.material,
            profile_knowledge=generation.profile_knowledge,
        ).model_dump_json()

        quiz = QuizService.save_generated_quiz(
            db,
            course.id,
            generation.quiz,
            user_id=current_user.id,
            model_used=generation.model_used,
            generation_settings=applied_settings,
            generation_context=applied_context,
        )

        view = QuizService.build_quiz_view(quiz)

        persisted = GeneratedOutputService.record(
            db,
            course_id=course.id,
            user_id=current_user.id,
            output_type="quiz",
            content=view.model_dump_json(),
            model_used=generation.model_used,
            generation_settings=applied_settings,
            generation_context=applied_context,
        )

    except (
        TextGenerationError,
        QuizGenerationError,
        RetrievalMaterialError,
        Exception,
    ) as exc:
        raise ai_generation_http_exception(exc, feature="quiz") from exc

    return BaseResponse(
        success=True,
        message="Quiz generated successfully",
        data=QuizGenerationResult(
            quiz=view,
            generated_output_id=persisted.id,
            context_truncated=generation.material.truncated,
            chunks_used=generation.material.chunks_used,
            chunks_available=generation.material.chunks_available,
            retrieval_narrowed=generation.material.retrieval_narrowed,
            lowest_similarity=generation.material.lowest_similarity,
            highest_similarity=generation.material.highest_similarity,
            profile_knowledge_used=bool(
                generation.profile_knowledge
                and not generation.profile_knowledge.is_empty
            ),
            profile_knowledge_items_used=(
                generation.profile_knowledge.items_used
                if generation.profile_knowledge
                else 0
            ),
        ),
    )


@router.get(
    "/{course_id}/quizzes",
    response_model=BaseResponse[list[QuizSummary]],
    responses={
        401: {"description": "Authentication required"},
        404: {"description": "Course not found"},
    },
)
def list_quizzes(
    course: AuthorizedCourse,
    db: Annotated[Session, Depends(get_db)],
) -> BaseResponse[list[QuizSummary]]:
    """List the quizzes of a course the caller is allowed to read."""
    rows = QuizService.list_course_quizzes(db, course.id)

    return BaseResponse(
        success=True,
        message="Quizzes retrieved successfully",
        data=[
            QuizService.build_quiz_summary(
                quiz,
                question_count,
                attempts_count=attempts_count,
                best_score=best_score,
                last_score=last_score,
            )
            for quiz, question_count, attempts_count, best_score, last_score in rows
        ],
    )


@router.get(
    "/{course_id}/quizzes/{quiz_id}",
    response_model=BaseResponse[QuizView],
    responses={
        401: {"description": "Authentication required"},
        404: {"description": "Course or quiz not found"},
    },
)
def get_quiz(
    quiz_id: int,
    course: AuthorizedCourse,
    db: Annotated[Session, Depends(get_db)],
) -> BaseResponse[QuizView]:
    """Return one stored quiz, questions in stable order, without regenerating."""
    quiz = QuizService.get_course_quiz(db, course.id, quiz_id)

    return BaseResponse(
        success=True,
        message="Quiz retrieved successfully",
        data=QuizService.build_quiz_view(quiz),
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
        provider_factory=lambda: _provider_for(None, current_user.preferred_model),
    )

    return BaseResponse(
        success=True,
        message="Quiz attempt recorded successfully",
        data=attempt,
    )


@router.get(
    "/{course_id}/quizzes/{quiz_id}/attempts",
    response_model=BaseResponse[list[QuizHistoryItem]],
    responses={
        401: {"description": "Authentication required"},
        404: {"description": "Course or quiz not found"},
    },
)
def list_quiz_attempts(
    quiz_id: int,
    course: AuthorizedCourse,
    db: Annotated[Session, Depends(get_db)],
) -> BaseResponse[list[QuizHistoryItem]]:
    """List attempts for one quiz in an authorized course."""
    items = QuizAttemptService.list_quiz_attempts(
        db,
        course.id,
        quiz_id,
        user_id=course.owner_id,
    )

    return BaseResponse(
        success=True,
        message="Quiz attempts retrieved successfully",
        data=items,
    )


@router.get(
    "/{course_id}/quizzes/{quiz_id}/attempts/{attempt_id}",
    response_model=BaseResponse[QuizAttemptResponse],
    responses={
        401: {"description": "Authentication required"},
        404: {"description": "Course, quiz, or attempt not found"},
    },
)
def get_quiz_attempt(
    quiz_id: int,
    attempt_id: int,
    course: AuthorizedCourse,
    db: Annotated[Session, Depends(get_db)],
) -> BaseResponse[QuizAttemptResponse]:
    """Retrieve full per-question review for one stored quiz attempt."""
    attempt = QuizAttemptService.get_attempt_detail(
        db,
        course.id,
        quiz_id,
        attempt_id,
        user_id=course.owner_id,
    )

    return BaseResponse(
        success=True,
        message="Quiz attempt retrieved successfully",
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
    db: Annotated[Session, Depends(get_db)],
):
    progress = QuizAttemptService.get_course_progress(
        db,
        course.id,
        user_id=course.owner_id,
    )

    return BaseResponse(
        success=True,
        message="Course progress retrieved successfully",
        data=progress,
    )
