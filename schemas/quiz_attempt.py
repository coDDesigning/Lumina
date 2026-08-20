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
    text_response: str | None = Field(default=None, max_length=10000)
    time_spent_seconds: int | None = Field(
        default=None,
        ge=0,
        le=MAX_TIME_SPENT_SECONDS,
    )


class QuizAttemptRequest(BaseModel):
    answers: list[QuizAnswerSubmission] = Field(min_length=1)
    time_spent_seconds: int | None = Field(
        default=None,
        ge=0,
        le=MAX_TIME_SPENT_SECONDS,
    )


class QuizAnswerResult(BaseModel):
    question_id: int
    selected_option_index: int | None = None
    text_response: str | None = None
    correct_option_index: int | None = None
    is_correct: bool | None = None
    time_spent_seconds: int | None = None
    topic: str | None = None


class QuizHistoryItem(BaseModel):
    attempt_id: int
    quiz_id: int
    score: float
    correct_count: int
    total_questions: int
    time_spent_seconds: int | None = None
    created_at: datetime


class QuizAttemptResponse(BaseModel):
    attempt_id: int
    quiz_id: int
    score: float
    correct_count: int
    total_questions: int
    time_spent_seconds: int | None = None
    created_at: datetime
    answers: list[QuizAnswerResult]


class TopicMastery(BaseModel):
    topic: str
    questions_answered: int
    questions_correct: int
    mastery_percentage: int
    status: MasteryStatus


class CourseProgressResponse(BaseModel):
    quizzes_completed: int = 0
    attempts_count: int = 0
    average_score: float | None = None
    correct_count: int = 0
    incorrect_count: int = 0
    total_questions_answered: int = 0
    completion: float = 0.0
    weak_topics: list[str] = Field(default_factory=list)
    topic_mastery: list[TopicMastery] = Field(default_factory=list)
    quiz_history: list[QuizHistoryItem] = Field(default_factory=list)
