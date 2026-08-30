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
    # Set when the student picked a source-derived question rather than typing a
    # topic; the evaluation then judges the explanation as an answer to it.
    question: str | None = Field(default=None, max_length=600)


class ReverseQuizEvaluation(BaseModel):
    feedback: str
    misconceptions: list[Misconception] = Field(default_factory=list)


class ReverseQuizResponse(BaseModel):
    id: int
    course_id: int
    topic: str
    explanation: str
    feedback: str
    misconceptions: list[Misconception] = Field(default_factory=list)
    question: str | None = None


class ReverseQuizQuestion(BaseModel):
    topic: str = Field(..., min_length=1, max_length=100)
    question: str = Field(..., min_length=1, max_length=600)


class ReverseQuizQuestionSet(BaseModel):
    """The provider's response for source-derived reverse-quiz questions."""

    questions: list[ReverseQuizQuestion] = Field(default_factory=list)


class ReverseQuizQuestionsResponse(BaseModel):
    course_id: int
    questions: list[ReverseQuizQuestion] = Field(default_factory=list)
