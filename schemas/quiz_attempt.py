from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from schemas.progress import CourseStatus
from schemas.quiz import QuizCorrectAnswer, QuizQuestionType, QuizView
from schemas.reverse_quiz import Misconception

MAX_TIME_SPENT_SECONDS = 86400
MAX_ANSWER_TEXT_CHARS = 10000
MASTERED_THRESHOLD = 80
NEEDS_REVIEW_THRESHOLD = 60

# An open-ended answer is scored as a fraction, so it needs a cut-off to become
# the boolean that topic mastery and the correct count are built from.
OPEN_ENDED_PASS_THRESHOLD = 0.6


class QuizSessionStatus(str, Enum):
    ACTIVE = "active"
    SUBMITTED = "submitted"
    EXPIRED = "expired"


class QuizSessionView(BaseModel):
    """One timed sitting as the client is allowed to see it.

    ``expires_at`` is the server's deadline and the only thing a countdown
    should be built from. ``seconds_remaining`` is a convenience derived from
    the same reading, never a second source of truth.
    """

    session_id: int
    quiz_id: int
    status: QuizSessionStatus
    started_at: datetime
    expires_at: datetime
    time_limit_seconds: int
    seconds_remaining: int
    elapsed_seconds: int
    answered_count: int = 0
    attempt_id: int | None = None


class QuizSessionStartResult(BaseModel):
    """A sitting and the paper to sit, with its answers withheld."""

    session: QuizSessionView
    quiz: QuizView


class MasteryStatus(str, Enum):
    MASTERED = "Mastered"
    IN_PROGRESS = "In Progress"
    NEEDS_REVIEW = "Needs Review"


class QuizAnswerSubmission(BaseModel):
    """One answer, in whichever form the question's type calls for.

    Option-based questions carry ``selected_option_index`` and text-based ones
    carry ``text_response``. Submitting the wrong one for a question type is
    rejected rather than silently graded as unanswered.
    """

    question_id: int = Field(ge=1)
    selected_option_index: int | None = Field(default=None, ge=0)
    text_response: str | None = Field(default=None, max_length=MAX_ANSWER_TEXT_CHARS)
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
    """The verdict for one answer.

    ``is_correct`` and ``score`` are both null when the answer could not be
    graded, which today means an open-ended answer the provider failed to score.
    An ungraded answer is excluded from the attempt score rather than counted
    wrong.
    """

    question_id: int
    question_type: QuizQuestionType
    selected_option_index: int | None = None
    text_response: str | None = None
    correct_option_index: int | None = None
    correct_answer: QuizCorrectAnswer | None = None
    is_correct: bool | None = None
    score: float | None = None
    feedback: str | None = None
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
    quiz_purpose: str | None = None
    timed: bool = False
    expired: bool = False


class QuizAttemptResponse(BaseModel):
    attempt_id: int
    quiz_id: int
    score: float
    correct_count: int
    graded_count: int
    total_questions: int
    time_spent_seconds: int | None = None
    created_at: datetime
    quiz_purpose: str | None = None
    timed: bool = False
    expired: bool = False
    answers: list[QuizAnswerResult]


class OpenEndedVerdict(BaseModel):
    """One open-ended answer scored by the text-generation provider.

    ``question_number`` refers to the position in the grading request, not to a
    quiz question index: the request only carries the open-ended answers, so the
    provider never sees identifiers belonging to the rest of the quiz.
    """

    model_config = ConfigDict(extra="ignore")

    question_number: int = Field(ge=1)
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    feedback: str = ""
    misconceptions: list[Misconception] = Field(default_factory=list)


class OpenEndedGradingResponse(BaseModel):
    verdicts: list[OpenEndedVerdict] = Field(min_length=1)


class TopicMastery(BaseModel):
    topic: str
    questions_answered: int
    questions_correct: int
    mastery_percentage: int
    status: MasteryStatus


class CourseProgressResponse(BaseModel):
    status: CourseStatus = "no_documents"
    quizzes_completed: int = 0
    attempts_count: int = 0
    average_score: float | None = None
    total_time_spent_seconds: int | None = None
    correct_count: int = 0
    incorrect_count: int = 0
    total_questions_answered: int = 0
    completion: float = 0.0
    weak_topics: list[str] = Field(default_factory=list)
    topic_mastery: list[TopicMastery] = Field(default_factory=list)
    quiz_history: list[QuizHistoryItem] = Field(default_factory=list)
