from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.database import get_db
from schemas.exam_roadmap import (
    ExamRoadmapRequest,
    ExamRoadmapResult,
)
from schemas.response import BaseResponse
from schemas.user import UserResponse
from services.exam_roadmap import ExamRoadmapService
from utils.ai_errors import ai_generation_http_exception
from utils.authorization import OwnedCourse
from utils.deps import get_current_user
from utils.rate_limit import rate_limit_generation

router = APIRouter(prefix="/api/courses", tags=["Exam Roadmap"])


@router.post(
    "/{course_id}/exam-roadmap",
    response_model=BaseResponse[ExamRoadmapResult],
    dependencies=[Depends(rate_limit_generation("exam_roadmap"))],
    responses={
        401: {"description": "Authentication required"},
        404: {"description": "Course not found"},
        409: {
            "description": (
                "The course has no exam date, its exam date has passed, or it "
                "has no topics to plan"
            )
        },
        422: {"description": "Invalid roadmap request"},
        429: {"description": "Per-user generation rate limited"},
        503: {"description": "Course search unreachable"},
        504: {"description": "Course search timed out"},
    },
)
def generate_exam_roadmap(
    course: OwnedCourse,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    request: ExamRoadmapRequest | None = None,
):
    """Plan every day from today to the exam date, and store the plan.

    No text-generation provider is called and no credit is charged: the schedule
    is derived from the course's own topics, exam date, and quiz history.
    Regenerating writes a new version and leaves earlier ones untouched.
    """
    try:
        generation = ExamRoadmapService.generate(
            db,
            course.id,
            request if request is not None else ExamRoadmapRequest(),
            user_id=current_user.id,
        )
        persisted = ExamRoadmapService.save_generated_output(
            db,
            course.id,
            generation,
            user_id=current_user.id,
        )
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise ai_generation_http_exception(exc, feature="exam_roadmap") from exc

    return BaseResponse(
        success=True,
        message="Exam roadmap generated successfully",
        data=ExamRoadmapResult(
            roadmap=generation.roadmap,
            generated_output_id=persisted.id,
        ),
    )
