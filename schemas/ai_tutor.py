from pydantic import BaseModel, Field

from schemas.generation import RetrievedContext


class AiTutorRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    conversation_id: int | None = Field(default=None, gt=0)
    model: str | None = None


class AiTutorResponse(BaseModel):
    answer: str


class AiTutorGenerationResult(RetrievedContext):
    answer: str
    conversation_id: int
