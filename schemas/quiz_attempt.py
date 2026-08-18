from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

MAX_TIME_SPENT_SECONDS = 86400
MASTERED_THRESHOLD = 80
NEEDS_REVIEW_THRESHOLD = 60


class MasteryStatus(str, Enum):
    MASTERED = "Mastered"
    IN_PROGRESS = "In Progress"
    NEEDS_REVIEW = "Needs Review"


class QuizAnswerSubmission(BaseModel):
    question_id: int = Field(ge=1)
    selected_option_index: int | None = Field(default=None, ge=0)


class QuizAttemptRequest(BaseModel):
    answers: list[QuizAnswerSubmission] = Field(min_length=1)
    time_spent_seconds: int | None = Field(
        default=None,
        ge=0,
        le=MAX_TIME_SPENT_SECONDS,
    )


class QuizAnswerResult(BaseModel):
    question_id: int
    selected_option_index: int | None
    correct_option_index: int
    is_correct: bool


class QuizAttemptResponse(BaseModel):
    attempt_id: int
    quiz_id: int
    score: float
    correct_count: int
    total_questions: int
    time_spent_seconds: int | None
    created_at: datetime
    answers: list[QuizAnswerResult]


class TopicMastery(BaseModel):
    topic: str
    questions_answered: int
    questions_correct: int
    mastery_percentage: int
    status: MasteryStatus


class CourseProgressResponse(BaseModel):
    attempts_count: int
    average_score: float | None
    topic_mastery: list[TopicMastery]
