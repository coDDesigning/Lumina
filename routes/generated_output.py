from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.database import get_db
from schemas.generated_output import GeneratedOutputDetail, GeneratedOutputSummary
from schemas.response import BaseResponse
from services.generated_output import GeneratedOutputService
from utils.authorization import AuthorizedCourse

router = APIRouter(prefix="/api/courses", tags=["Generated Outputs"])


@router.get(
    "/{course_id}/generated-outputs",
    response_model=BaseResponse[list[GeneratedOutputSummary]],
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Account is not allowed to access generated outputs"},
        404: {"description": "Course not found"},
    },
)
def get_generated_outputs(
    course: AuthorizedCourse,
    db: Annotated[Session, Depends(get_db)],
) -> BaseResponse[list[GeneratedOutputSummary]]:
    """List the generated outputs of a course the caller is allowed to read."""
    outputs = GeneratedOutputService.list_course_outputs(db, course.id)

    return BaseResponse(
        success=True,
        message="Generated outputs retrieved successfully",
        data=[GeneratedOutputService.summarize(output) for output in outputs],
    )


@router.get(
    "/{course_id}/generated-outputs/{output_id}",
    response_model=BaseResponse[GeneratedOutputDetail],
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Account is not allowed to access generated outputs"},
        404: {"description": "Course or generated output not found"},
    },
)
def get_generated_output(
    output_id: int,
    course: AuthorizedCourse,
    db: Annotated[Session, Depends(get_db)],
) -> BaseResponse[GeneratedOutputDetail]:
    """Return one stored generated output without regenerating anything."""
    output = GeneratedOutputService.get_course_output(db, course.id, output_id)

    return BaseResponse(
        success=True,
        message="Generated output retrieved successfully",
        data=GeneratedOutputService.detail(output),
    )
