"""Request, generation, storage, and view models for quizzes.

Generated questions are a discriminated union rather than one permissive model.
A provider response is untrusted input: making each question type its own strict
model is what stops a true/false question carrying a four-option list, or a
short-answer question carrying an option index, from ever reaching the database.

``QuizCorrectAnswer`` is the second union, and it is the document persisted in
``quiz_questions.correct_answer``. ``correct_option_index`` stays populated for
the two option-based types so existing grading and the frontend keep working,
but the document is the authoritative answer.
"""

import re
import unicodedata
from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from schemas.generation import RetrievalGenerationContext, RetrievedContext

MIN_QUIZ_QUESTIONS = 1
MAX_QUIZ_QUESTIONS = 20
MULTIPLE_CHOICE_OPTION_COUNT = 4
TRUE_FALSE_OPTION_COUNT = 2
TRUE_FALSE_OPTIONS: tuple[str, ...] = ("True", "False")
MAX_ACCEPTED_ANSWERS = 8
MAX_SHORT_ANSWER_CHARS = 200
MAX_TOPIC_CHARS = 200
MAX_TITLE_CHARS = 200

_PUNCTUATION = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WHITESPACE = re.compile(r"\s+")


def normalize_answer_text(value: str) -> str:
    """Reduce a free-text answer to the form short-answer grading compares.

    Case, surrounding punctuation, and whitespace runs carry no meaning in a
    one-line answer, so ``"  O(LOG N). "`` and ``"O(log n)"`` must agree.
    """
    folded = unicodedata.normalize("NFKC", value).casefold()
    return _WHITESPACE.sub(" ", _PUNCTUATION.sub(" ", folded)).strip()


class QuizQuestionType(str, Enum):
    MULTIPLE_CHOICE = "multiple_choice"
    TRUE_FALSE = "true_false"
    SHORT_ANSWER = "short_answer"
    OPEN_ENDED = "open_ended"


class QuizDifficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


OPTION_BASED_QUESTION_TYPES = frozenset(
    {QuizQuestionType.MULTIPLE_CHOICE, QuizQuestionType.TRUE_FALSE}
)


class QuizRequest(BaseModel):
    question_count: int = Field(ge=MIN_QUIZ_QUESTIONS, le=MAX_QUIZ_QUESTIONS)
    question_types: list[QuizQuestionType] = Field(
        min_length=1,
        max_length=len(QuizQuestionType),
        description="The question types the generated quiz may use.",
    )
    difficulty: QuizDifficulty
    topic_focus: str = Field(min_length=1, max_length=MAX_TOPIC_CHARS)
    model: str | None = Field(
        default=None,
        description="Explicit model override, or omit to use preferred/default model",
    )

    @field_validator("question_types")
    @classmethod
    def _unique_preserving_order(
        cls, value: list[QuizQuestionType]
    ) -> list[QuizQuestionType]:
        seen: set[QuizQuestionType] = set()
        unique: list[QuizQuestionType] = []
        for question_type in value:
            if question_type not in seen:
                seen.add(question_type)
                unique.append(question_type)
        return unique


class MultipleChoiceAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal[QuizQuestionType.MULTIPLE_CHOICE] = QuizQuestionType.MULTIPLE_CHOICE
    option_index: int = Field(ge=0)


class TrueFalseAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal[QuizQuestionType.TRUE_FALSE] = QuizQuestionType.TRUE_FALSE
    value: StrictBool


class ShortAnswerAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal[QuizQuestionType.SHORT_ANSWER] = QuizQuestionType.SHORT_ANSWER
    text: str = Field(min_length=1, max_length=MAX_SHORT_ANSWER_CHARS)
    accepted_answers: list[str] = Field(
        default_factory=list, max_length=MAX_ACCEPTED_ANSWERS
    )


class OpenEndedAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal[QuizQuestionType.OPEN_ENDED] = QuizQuestionType.OPEN_ENDED
    reference_answer: str = Field(min_length=1)


QuizCorrectAnswer = Annotated[
    MultipleChoiceAnswer | TrueFalseAnswer | ShortAnswerAnswer | OpenEndedAnswer,
    Field(discriminator="type"),
]


class GeneratedQuestionBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_number: int = Field(ge=1, le=MAX_QUIZ_QUESTIONS)
    topic: str = Field(min_length=1, max_length=MAX_TOPIC_CHARS)
    question: str = Field(min_length=1)
    difficulty: QuizDifficulty
    explanation: str

    def stored_options(self) -> list[str] | None:
        return None

    def stored_option_index(self) -> int | None:
        return None

    def stored_answer(self) -> BaseModel:
        raise NotImplementedError


class GeneratedMultipleChoiceQuestion(GeneratedQuestionBase):
    question_type: Literal[QuizQuestionType.MULTIPLE_CHOICE] = (
        QuizQuestionType.MULTIPLE_CHOICE
    )
    options: list[str] = Field(
        min_length=MULTIPLE_CHOICE_OPTION_COUNT,
        max_length=MULTIPLE_CHOICE_OPTION_COUNT,
    )
    correct_option_index: int = Field(ge=0, lt=MULTIPLE_CHOICE_OPTION_COUNT)

    @field_validator("options")
    @classmethod
    def _options_are_distinct_and_non_blank(cls, value: list[str]) -> list[str]:
        cleaned = [option.strip() for option in value]
        if any(not option for option in cleaned):
            raise ValueError("Every multiple choice option must be non-empty")
        if len({option.casefold() for option in cleaned}) != len(cleaned):
            raise ValueError("Multiple choice options must be distinct")
        return cleaned

    def stored_options(self) -> list[str] | None:
        return list(self.options)

    def stored_option_index(self) -> int | None:
        return self.correct_option_index

    def stored_answer(self) -> MultipleChoiceAnswer:
        return MultipleChoiceAnswer(option_index=self.correct_option_index)


class GeneratedTrueFalseQuestion(GeneratedQuestionBase):
    question_type: Literal[QuizQuestionType.TRUE_FALSE] = QuizQuestionType.TRUE_FALSE
    correct_answer: StrictBool

    def stored_options(self) -> list[str] | None:
        return list(TRUE_FALSE_OPTIONS)

    def stored_option_index(self) -> int | None:
        return TRUE_FALSE_OPTIONS.index("True" if self.correct_answer else "False")

    def stored_answer(self) -> TrueFalseAnswer:
        return TrueFalseAnswer(value=self.correct_answer)


class GeneratedShortAnswerQuestion(GeneratedQuestionBase):
    question_type: Literal[QuizQuestionType.SHORT_ANSWER] = (
        QuizQuestionType.SHORT_ANSWER
    )
    correct_answer: str = Field(min_length=1, max_length=MAX_SHORT_ANSWER_CHARS)
    accepted_answers: list[str] = Field(
        default_factory=list, max_length=MAX_ACCEPTED_ANSWERS
    )

    @field_validator("correct_answer")
    @classmethod
    def _correct_answer_is_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("A short answer question needs a non-blank answer")
        return stripped

    def accepted_variants(self) -> list[str]:
        """Every spelling that counts as correct, primary answer first.

        Normalized deduplication happens here rather than at grading time so the
        stored document is already the set grading compares against.
        """
        variants: list[str] = []
        seen: set[str] = set()
        for candidate in (self.correct_answer, *self.accepted_answers):
            stripped = candidate.strip()
            if not stripped:
                continue
            key = normalize_answer_text(stripped)
            if not key or key in seen:
                continue
            seen.add(key)
            variants.append(stripped)
        return variants

    def stored_answer(self) -> ShortAnswerAnswer:
        variants = self.accepted_variants()
        return ShortAnswerAnswer(text=variants[0], accepted_answers=variants)


class GeneratedOpenEndedQuestion(GeneratedQuestionBase):
    question_type: Literal[QuizQuestionType.OPEN_ENDED] = QuizQuestionType.OPEN_ENDED
    reference_answer: str = Field(min_length=1)

    @field_validator("reference_answer")
    @classmethod
    def _reference_answer_is_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError(
                "An open ended question needs a non-blank reference answer"
            )
        return stripped

    def stored_answer(self) -> OpenEndedAnswer:
        return OpenEndedAnswer(reference_answer=self.reference_answer)


GeneratedQuizQuestion = Annotated[
    GeneratedMultipleChoiceQuestion
    | GeneratedTrueFalseQuestion
    | GeneratedShortAnswerQuestion
    | GeneratedOpenEndedQuestion,
    Field(discriminator="question_type"),
]


class QuizGenerationResponse(BaseModel):
    title: str = Field(min_length=1, max_length=MAX_TITLE_CHARS)
    questions: list[GeneratedQuizQuestion] = Field(
        min_length=MIN_QUIZ_QUESTIONS,
        max_length=MAX_QUIZ_QUESTIONS,
    )


class QuizGenerationSettings(BaseModel):
    """The options a stored quiz was generated with."""

    model_config = ConfigDict(extra="ignore")

    version: Literal[1] = 1
    output_type: Literal["quiz"] = "quiz"
    question_count: int
    question_types: list[QuizQuestionType]
    difficulty: QuizDifficulty
    topic_focus: str
    retrieval_limit: int
    retrieval_min_similarity: float

    @classmethod
    def from_request(
        cls,
        request: QuizRequest,
        *,
        retrieval_limit: int,
        retrieval_min_similarity: float,
    ) -> "QuizGenerationSettings":
        return cls(
            question_count=request.question_count,
            question_types=list(request.question_types),
            difficulty=request.difficulty,
            topic_focus=request.topic_focus,
            retrieval_limit=retrieval_limit,
            retrieval_min_similarity=retrieval_min_similarity,
        )


class QuizGenerationContext(RetrievalGenerationContext):
    """What retrieval actually produced for a stored quiz."""


class QuizQuestionView(BaseModel):
    question_id: int
    question_number: int
    question_type: QuizQuestionType
    difficulty: QuizDifficulty | None = None
    topic: str
    question: str
    options: list[str] | None = None
    correct_option_index: int | None = None
    correct_answer: QuizCorrectAnswer | None = None
    explanation: str


class QuizView(BaseModel):
    quiz_id: int
    course_id: int
    title: str
    created_at: datetime
    user_id: int | None = None
    model_used: str | None = None
    generation_settings: dict | None = None
    generation_context: dict | None = None
    questions: list[QuizQuestionView]


class QuizSummary(BaseModel):
    """One stored quiz without its questions, for history listings."""

    quiz_id: int
    course_id: int
    title: str
    question_count: int
    created_at: datetime
    user_id: int | None = None
    model_used: str | None = None
    generation_settings: dict | None = None
    generation_context: dict | None = None


class QuizGenerationResult(RetrievedContext):
    quiz: QuizView
    generated_output_id: int
