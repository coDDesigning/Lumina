from enum import Enum

from pydantic import BaseModel, Field, model_validator

from schemas.generation import BoundedContext

MIN_QUIZ_QUESTIONS = 1
MAX_QUIZ_QUESTIONS = 20
TRUE_FALSE_OPTION_COUNT = 2


class QuizQuestionType(str, Enum):
    MULTIPLE_CHOICE = "multiple_choice"
    TRUE_FALSE = "true_false"


class QuizDifficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class QuizRequest(BaseModel):
    question_count: int = Field(ge=MIN_QUIZ_QUESTIONS, le=MAX_QUIZ_QUESTIONS)
    question_type: QuizQuestionType
    difficulty: QuizDifficulty
    topic_focus: str = Field(min_length=1, max_length=200)
    model: str | None = Field(
        default=None,
        description="Explicit model override, or omit to use preferred/default model",
    )


class GeneratedQuizQuestion(BaseModel):
    question_number: int = Field(ge=1, le=MAX_QUIZ_QUESTIONS)
    topic: str
    question: str
    options: list[str] = Field(min_length=TRUE_FALSE_OPTION_COUNT, max_length=4)
    correct_option_index: int = Field(ge=0)
    explanation: str

    @model_validator(mode="after")
    def _correct_option_index_selects_an_option(self) -> "GeneratedQuizQuestion":
        if self.correct_option_index >= len(self.options):
            raise ValueError("correct_option_index must select one of the options")
        return self


class QuizGenerationResponse(BaseModel):
    title: str
    questions: list[GeneratedQuizQuestion] = Field(
        min_length=MIN_QUIZ_QUESTIONS,
        max_length=MAX_QUIZ_QUESTIONS,
    )


class QuizQuestionView(BaseModel):
    question_id: int
    question_number: int
    topic: str
    question: str
    options: list[str]
    correct_option_index: int
    explanation: str


class QuizView(BaseModel):
    quiz_id: int
    title: str
    questions: list[QuizQuestionView]


class QuizGenerationResult(BoundedContext):
    quiz: QuizView
