from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.database import get_db
from schemas.generation_job import GenerationJobAccepted, GenerationJobView
from schemas.response import BaseResponse
from schemas.user import UserResponse
from services.generation_jobs import (
    GenerationJobNotRetryableError,
    get_generation_job,
    list_course_generation_jobs,
    retry_generation_job,
)
from utils.ai_errors import ai_generation_http_exception
from utils.authorization import AuthorizedCourse, OwnedCourse
from utils.deps import get_current_user
from utils.rate_limit import rate_limit_generation

router = APIRouter(prefix="/api/courses", tags=["Generation Jobs"])


@router.get(
    "/{course_id}/generation-jobs",
    response_model=BaseResponse[list[GenerationJobView]],
    responses={
        401: {"description": "Authentication required"},
        404: {"description": "Course not found"},
    },
)
def list_generation_jobs(
    course: AuthorizedCourse,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> BaseResponse[list[GenerationJobView]]:
    """The caller's unfinished and recently finished generations for a course.

    This is what rebuilds the panel after a reload, so it is the reason closing
    the tab no longer loses a running generation. It is scoped to the caller,
    never the course, because a generation belongs to whoever paid for it.
    """
    jobs = list_course_generation_jobs(db, course.id, current_user.id)

    return BaseResponse(
        success=True,
        message="Generation jobs retrieved successfully",
        data=[GenerationJobView.from_job(job) for job in jobs],
    )


@router.get(
    "/{course_id}/generation-jobs/{job_id}",
    response_model=BaseResponse[GenerationJobView],
    responses={
        401: {"description": "Authentication required"},
        404: {"description": "Course or generation job not found"},
    },
)
def get_generation_job_status(
    job_id: int,
    course: AuthorizedCourse,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> BaseResponse[GenerationJobView]:
    """One generation's current state, which is what the client polls."""
    job = get_generation_job(db, course.id, current_user.id, job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Generation job not found",
        )

    return BaseResponse(
        success=True,
        message="Generation job retrieved successfully",
        data=GenerationJobView.from_job(job),
    )


@router.post(
    "/{course_id}/generation-jobs/{job_id}/retry",
    response_model=BaseResponse[GenerationJobAccepted],
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit_generation("generation_job_retry"))],
    responses={
        401: {"description": "Authentication required"},
        402: {"description": "Insufficient credits"},
        404: {"description": "Course or generation job not found"},
        409: {"description": "Generation job is not retryable"},
        429: {"description": "Per-user generation rate limited"},
    },
)
def retry_failed_generation_job(
    job_id: int,
    course: OwnedCourse,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> BaseResponse[GenerationJobAccepted]:
    try:
        job = retry_generation_job(
            db,
            course_id=course.id,
            user_id=current_user.id,
            job_id=job_id,
        )
    except GenerationJobNotRetryableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise ai_generation_http_exception(exc, feature="generation_job_retry") from exc
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Generation job not found",
        )

    return BaseResponse(
        success=True,
        message="Generation queued again",
        data=GenerationJobAccepted(job_id=job.id, status=job.status),
    )
