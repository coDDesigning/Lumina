from pydantic import BaseModel, Field

from schemas.generation import RetrievedContext


class CourseQARequest(BaseModel):
    question: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="The question about course materials",
    )
    conversation_id: int | None = Field(
        default=None,
        gt=0,
        description="Existing conversation to continue, or omit to start a new one",
    )
    model: str | None = Field(
        default=None,
        description="Explicit model override, or omit to use preferred/default model",
    )


class CourseQAResponse(BaseModel):
    answer: str


class CourseQAGenerationResult(RetrievedContext):
    answer: str
    conversation_id: int
