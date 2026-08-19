from pydantic import BaseModel, Field

from schemas.generation import BoundedContext


class CourseQARequest(BaseModel):
    question: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="The question about course materials",
    )


class CourseQAResponse(BaseModel):
    answer: str


class CourseQAGenerationResult(BoundedContext):
    answer: str
