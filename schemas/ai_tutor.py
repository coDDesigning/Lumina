from pydantic import BaseModel

from schemas.generation import BoundedContext


class AiTutorRequest(BaseModel):
    question: str


class AiTutorResponse(BaseModel):
    answer: str


class AiTutorGenerationResult(BoundedContext):
    answer: str
