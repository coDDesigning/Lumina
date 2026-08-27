from enum import Enum
from pydantic import BaseModel, Field

class ConceptStatus(str, Enum):
    UNSUPPORTED = "unsupported"
    ABSENT = "absent"
    PARTIALLY_CORRECT = "partially_correct"
    CONTRADICTED = "contradicted"

class Misconception(BaseModel):
    concept: str
    status: ConceptStatus
    detail: str

class ReverseQuizRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=100)
    explanation: str = Field(..., min_length=1, max_length=5000)

class ReverseQuizResponse(BaseModel):
    id: int
    course_id: int
    topic: str
    explanation: str
    feedback: str
    misconceptions: list[Misconception] = Field(default_factory=list)
