from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models import ANSWER_HIDDEN_QUIZ_PURPOSES, JOB_TYPE_GENERATE_QUIZ, User
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
    QuizAnswerSubmission,
    QuizSessionStartResult,
    QuizSessionView,
    CourseProgressResponse,
    QuizAttemptRequest,
    QuizAttemptResponse,
    QuizHistoryItem,
)
from schemas.generation_job import GenerationJobAccepted
from schemas.response import BaseResponse
from schemas.user import UserResponse
from services.credits import CreditService
from services.generation_jobs import enqueue_generation_job
from services.quiz import QuizService
from services.quiz_attempt import QuizAttemptService
from services.quiz_session import (
    QuizSessionService,
    SessionState,
    TimedSessionEmptyError,
    TimedSessionExpiredError,
    TimedSessionSubmittedError,
    is_timed,
)
from services.text_generation import (
    get_text_generation_provider,
    resolve_effective_model,
)
from utils.ai_errors import ERROR_CODE_HEADER, ai_generation_http_exception
from utils.authorization import AuthorizedCourse, OwnedCourse
from utils.deps import get_current_user
from utils.rate_limit import rate_limit_generation

router = APIRouter(
    prefix="/api/courses",
    tags=["Quiz"],
)


def _provider_for(
    model: str | None,
    preferred_model: str | None,
    user: object | None = None,
):
    effective_model = resolve_effective_model(
        model, preferred_model, required_capability="quiz"
    )
    return get_text_generation_provider(
        effective_model=effective_model,
        user=user,
        require_json_mode=True,
    )


def _grading_provider_for(
    preferred_model: str | None,
    user: object | None = None,
):
    """A grader bounded to finish before the database closes the transaction.

    Grading runs inside the transaction that writes the attempt. The generation
    budget is longer than a hosted database will leave a transaction idle, so a
    slow grader would take the student's answers down with it; this trades an
    ungraded written answer, which the attempt already handles, for a lost one.
    """
    effective_model = resolve_effective_model(
        None, preferred_model, required_capability="quiz"
    )
    return get_text_generation_provider(
        effective_model=effective_model,
        user=user,
        require_json_mode=True,
        overall_timeout_seconds=settings.ai_grading_overall_timeout_seconds,
    )


# Stable categories a client can branch on. Timed-sitting failures are not
# generation failures, so they carry their own codes rather than borrowing the
# AI error vocabulary.
ERROR_TIMED_SESSION_REQUIRED = "timed_session_required"
ERROR_TIMED_SESSION_EXPIRED = "timed_session_expired"
ERROR_TIMED_SESSION_SUBMITTED = "timed_session_already_submitted"
ERROR_TIMED_SESSION_EMPTY = "timed_session_empty"

SESSION_RESPONSES = {
    401: {"description": "Authentication required"},
    404: {"description": "Course, quiz, or session not found"},
    409: {"description": "The session cannot be used in its current state"},
}


def _session_view(state: SessionState) -> QuizSessionView:
    return QuizSessionView(
        session_id=state.session_id,
        quiz_id=state.quiz_id,
        status=state.status,
        started_at=state.started_at,
        expires_at=state.expires_at,
        time_limit_seconds=state.time_limit_seconds,
        seconds_remaining=state.seconds_remaining,
        elapsed_seconds=state.elapsed_seconds,
        answered_count=state.answered_count,
        answers=list(state.answers),
        attempt_id=state.attempt_id,
    )


def _session_conflict(exc: Exception, code: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=str(exc),
        headers={ERROR_CODE_HEADER: code},
    )


@router.post(
    "/{course_id}/quiz",
    response_model=BaseResponse[QuizGenerationResult],
    dependencies=[Depends(rate_limit_generation("quiz"))],
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
        429: {"description": "AI provider or per-user generation rate limited"},
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
    generation = None
    try:
        db_user = db.get(User, current_user.id)
        provider = _provider_for(
            request.model,
            current_user.preferred_model,
            user=db_user,
        )

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

        persisted_quiz = QuizService.save_generated_quiz(
            db,
            course.id,
            generation.quiz,
            user_id=current_user.id,
            model_used=generation.model_used,
            generation_settings=applied_settings,
            generation_context=applied_context,
            citations=generation.material.citation_map,
        )

    except HTTPException:
        raise
    except Exception as exc:
        if generation is not None:
            db.rollback()
            CreditService.refund(db, generation.charge_receipt)
        raise ai_generation_http_exception(exc, feature="quiz") from exc

    return BaseResponse(
        success=True,
        message="Quiz generated successfully",
        data=QuizGenerationResult(
            quiz=persisted_quiz.view,
            generated_output_id=persisted_quiz.generated_output.id,
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
    """Return one stored quiz, questions in stable order, without regenerating.

    An assessment is served with its answers withheld. Exam Mode redacts its own
    responses, but this endpoint reads the same rows, so without the same rule
    here a candidate could simply ask for the quiz and read the answers off it.
    Practice keeps its answers, because immediate feedback is its whole point.
    """
    quiz = QuizService.get_course_quiz(db, course.id, quiz_id)
    view = QuizService.build_quiz_view(quiz)
    if quiz.purpose in ANSWER_HIDDEN_QUIZ_PURPOSES:
        view = QuizService.hide_answers(view)

    return BaseResponse(
        success=True,
        message="Quiz retrieved successfully",
        data=view,
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
    """Record one attempt at an untimed quiz.

    A quiz with a time limit is refused here. Its elapsed time has to be
    measured by the server, and this endpoint would take the client's word for
    it, which is the whole thing a timed sitting exists to prevent.
    """
    quiz = QuizService.get_course_quiz(db, course.id, quiz_id)
    if is_timed(quiz):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This quiz is sat against a clock. Start an exam session first.",
            headers={ERROR_CODE_HEADER: ERROR_TIMED_SESSION_REQUIRED},
        )

    db_user = db.get(User, current_user.id)
    attempt = QuizAttemptService.record_attempt(
        db,
        course.id,
        quiz_id,
        request,
        user_id=current_user.id,
        provider_factory=lambda: _grading_provider_for(
            current_user.preferred_model,
            user=db_user,
        ),
    )

    return BaseResponse(
        success=True,
        message="Quiz attempt recorded successfully",
        data=attempt,
    )


@router.post(
    "/{course_id}/quizzes/{quiz_id}/sessions",
    response_model=BaseResponse[QuizSessionStartResult],
    status_code=status.HTTP_201_CREATED,
    responses=SESSION_RESPONSES,
)
def start_quiz_session(
    course: OwnedCourse,
    quiz_id: int,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Open a timed sitting, or rejoin the one already running.

    The deadline in the response is the server's own and the only one that
    counts. A reloaded page rejoins its sitting rather than starting a second
    clock, so the drafts stay together.
    """
    state = QuizSessionService.start_session(
        db, course.id, quiz_id, user_id=current_user.id
    )
    quiz = QuizService.get_course_quiz(db, course.id, quiz_id)
    return BaseResponse(
        success=True,
        message="Exam session started successfully",
        data=QuizSessionStartResult(
            session=_session_view(state),
            quiz=QuizService.hide_answers(QuizService.build_quiz_view(quiz)),
        ),
    )


@router.get(
    "/{course_id}/quizzes/{quiz_id}/sessions/{session_id}",
    response_model=BaseResponse[QuizSessionView],
    responses=SESSION_RESPONSES,
)
def read_quiz_session(
    course: OwnedCourse,
    quiz_id: int,
    session_id: int,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Report a sitting, including one whose deadline has quietly passed."""
    state = QuizSessionService.get_session(
        db, course.id, quiz_id, session_id, user_id=current_user.id
    )
    return BaseResponse(
        success=True,
        message="Exam session retrieved successfully",
        data=_session_view(state),
    )


@router.put(
    "/{course_id}/quizzes/{quiz_id}/sessions/{session_id}/answers/{question_id}",
    response_model=BaseResponse[QuizSessionView],
    responses={
        **SESSION_RESPONSES,
        400: {"description": "The answer is not valid for this question"},
    },
)
def save_quiz_session_answer(
    course: OwnedCourse,
    quiz_id: int,
    session_id: int,
    question_id: int,
    request: QuizAnswerSubmission,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Save one answer as it stands, so the deadline cannot cost the student it."""
    answer = request.model_copy(update={"question_id": question_id})
    try:
        state = QuizSessionService.save_draft_answer(
            db, course.id, quiz_id, session_id, answer, user_id=current_user.id
        )
    except TimedSessionExpiredError as exc:
        raise _session_conflict(exc, ERROR_TIMED_SESSION_EXPIRED) from exc
    except TimedSessionSubmittedError as exc:
        raise _session_conflict(exc, ERROR_TIMED_SESSION_SUBMITTED) from exc

    return BaseResponse(
        success=True,
        message="Answer saved successfully",
        data=_session_view(state),
    )


@router.post(
    "/{course_id}/quizzes/{quiz_id}/sessions/{session_id}/submit",
    response_model=BaseResponse[QuizAttemptResponse],
    status_code=status.HTTP_201_CREATED,
    responses=SESSION_RESPONSES,
)
def submit_quiz_session(
    course: OwnedCourse,
    quiz_id: int,
    session_id: int,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Finalise a sitting into an ordinary attempt, exactly once.

    What is graded is the answers saved before the deadline. Submitting twice
    returns the same attempt rather than grading the paper again.
    """
    try:
        db_user = db.get(User, current_user.id)
        attempt = QuizSessionService.submit_session(
            db,
            course.id,
            quiz_id,
            session_id,
            user_id=current_user.id,
            provider_factory=lambda: _grading_provider_for(
                current_user.preferred_model,
                user=db_user,
            ),
        )
    except TimedSessionEmptyError as exc:
        raise _session_conflict(exc, ERROR_TIMED_SESSION_EMPTY) from exc
    except TimedSessionSubmittedError as exc:
        raise _session_conflict(exc, ERROR_TIMED_SESSION_SUBMITTED) from exc

    return BaseResponse(
        success=True,
        message="Exam session submitted successfully",
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


@router.post(
    "/{course_id}/quiz/jobs",
    response_model=BaseResponse[GenerationJobAccepted],
    status_code=202,
    dependencies=[Depends(rate_limit_generation("quiz"))],
    responses={
        401: {"description": "Authentication required"},
        402: {"description": "Insufficient credits"},
        404: {"description": "Course not found"},
        422: {"description": "Invalid quiz request"},
        429: {"description": "Per-user generation rate limited"},
    },
)
def enqueue_quiz(
    course: OwnedCourse,
    request: QuizRequest,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Queue a quiz and return immediately with a handle to poll.

    The price is settled here rather than in the worker, because it depends on
    the question types the student asked for and those are known now; a quiz
    that turns out to be open-ended has already been charged as one.
    """
    try:
        effective_model = resolve_effective_model(
            request.model, current_user.preferred_model, required_capability="quiz"
        )
        queued_request = request.model_copy(update={"model": effective_model})
        job = enqueue_generation_job(
            db,
            course_id=course.id,
            user_id=current_user.id,
            job_type=JOB_TYPE_GENERATE_QUIZ,
            request_payload=queued_request.model_dump_json(),
            credit_cost=QuizService.credit_cost(request),
        )
    except Exception as exc:
        raise ai_generation_http_exception(exc, feature="quiz") from exc

    return BaseResponse(
        success=True,
        message="Quiz generation queued",
        data=GenerationJobAccepted(job_id=job.id, status=job.status),
    )
