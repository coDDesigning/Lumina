from pydantic import BaseModel

from schemas.generation import BoundedContext


class AiTutorRequest(BaseModel):
    question: str
    model: str | None = None


class AiTutorResponse(BaseModel):
    answer: str


class AiTutorGenerationResult(BoundedContext):
    answer: str
