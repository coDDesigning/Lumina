"""Retrieval-backed quiz generation, persistence, and reads.

Generation is atomic in the sense that matters: the provider's whole response is
validated, and checked against the request that asked for it, before a single
row is written. A malformed or non-conforming response leaves no quiz and no
questions behind, and the questions of a quiz are written in the same
transaction as the quiz itself.

Material comes from course-isolated semantic retrieval. There is deliberately no
fallback to whole-corpus assembly: a retrieval failure fails the request rather
than quietly widening it to material the student did not ask about.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from backend.app.config import settings
from backend.app.models import Course, Quiz, QuizQuestion
from schemas.ai_usage import ErrorCategory, GenerationType
from schemas.quiz import (
    MULTIPLE_CHOICE_OPTION_COUNT,
    QuizCorrectAnswer,
    QuizDifficulty,
    QuizGenerationResponse,
    QuizQuestionType,
    QuizQuestionView,
    QuizRequest,
    QuizSummary,
    QuizView,
)
from services.ai_usage_logger import AiUsageLogger
from services.course_material import count_available_chunks
from services.profile_knowledge import (
    ProfileKnowledgeContext,
    assemble_generation_context,
)
from services.prompt_loader import PromptLoader
from services.retrieval_material import (
    MaterialNotIndexedError,
    MaterialRetrievalError,
    NoRelevantMaterialError,
    RetrievedCourseMaterial,
    load_retrieved_material,
)
from services.retrieval_query import build_retrieval_query
from services.text_generation import (
    TextGenerationError,
    TextGenerationProvider,
    model_identifier,
)
from services.credits import CreditService
from utils.ai_errors import (
    NO_READY_MATERIAL_MESSAGE,
    CourseMaterialUnavailableError,
    InsufficientCreditsError,
    InvalidGeneratedStructureError,
)
from utils.exceptions import NotFoundException
from utils.json_documents import parse_json_object

logger = logging.getLogger(__name__)

QUIZ_NOT_FOUND = "Quiz not found"

_CORRECT_ANSWER_ADAPTER: TypeAdapter[QuizCorrectAnswer] = TypeAdapter(QuizCorrectAnswer)


QUESTION_TYPE_DIRECTIVES: dict[QuizQuestionType, str] = {
    QuizQuestionType.MULTIPLE_CHOICE: (
        "multiple_choice: exactly "
        f"{MULTIPLE_CHOICE_OPTION_COUNT} distinct answer choices, with "
        "correct_option_index giving the zero-based position of the only correct "
        "choice. Distractors must be plausible and drawn from the material."
    ),
    QuizQuestionType.TRUE_FALSE: (
        "true_false: a single statement that is either true or false, with "
        "correct_answer as the JSON boolean true or false. Never the strings "
        '"true" or "false", and never an options array.'
    ),
    QuizQuestionType.SHORT_ANSWER: (
        "short_answer: a question answerable in a few words, with correct_answer "
        "as the expected answer and accepted_answers listing other spellings or "
        "phrasings that should also count as correct."
    ),
    QuizQuestionType.OPEN_ENDED: (
        "open_ended: a question calling for a written explanation, with "
        "reference_answer describing what a strong response must contain. There "
        "is no single exact answer and no options array."
    ),
}

QUESTION_TYPE_SCHEMAS: dict[QuizQuestionType, str] = {
    QuizQuestionType.MULTIPLE_CHOICE: (
        "{\n"
        '  "question_number": 1,\n'
        '  "question_type": "multiple_choice",\n'
        '  "topic": "...",\n'
        '  "question": "...",\n'
        '  "difficulty": "easy | medium | hard",\n'
        '  "options": ["...", "...", "...", "..."],\n'
        '  "correct_option_index": 0,\n'
        '  "explanation": "..."\n'
        "}"
    ),
    QuizQuestionType.TRUE_FALSE: (
        "{\n"
        '  "question_number": 1,\n'
        '  "question_type": "true_false",\n'
        '  "topic": "...",\n'
        '  "question": "...",\n'
        '  "difficulty": "easy | medium | hard",\n'
        '  "correct_answer": true,\n'
        '  "explanation": "..."\n'
        "}"
    ),
    QuizQuestionType.SHORT_ANSWER: (
        "{\n"
        '  "question_number": 1,\n'
        '  "question_type": "short_answer",\n'
        '  "topic": "...",\n'
        '  "question": "...",\n'
        '  "difficulty": "easy | medium | hard",\n'
        '  "correct_answer": "...",\n'
        '  "accepted_answers": ["...", "..."],\n'
        '  "explanation": "..."\n'
        "}"
    ),
    QuizQuestionType.OPEN_ENDED: (
        "{\n"
        '  "question_number": 1,\n'
        '  "question_type": "open_ended",\n'
        '  "topic": "...",\n'
        '  "question": "...",\n'
        '  "difficulty": "easy | medium | hard",\n'
        '  "reference_answer": "...",\n'
        '  "explanation": "..."\n'
        "}"
    ),
}

DIFFICULTY_DIRECTIVES: dict[QuizDifficulty, str] = {
    QuizDifficulty.EASY: (
        "Every question must be easy: direct recall or straightforward comprehension "
        "of the lecture material."
    ),
    QuizDifficulty.MEDIUM: (
        "Every question must be of medium difficulty: applying or comparing ideas from "
        "the lecture material."
    ),
    QuizDifficulty.HARD: (
        "Every question must be hard: analysis, synthesis, or reasoning that combines "
        "several parts of the lecture material."
    ),
}


class QuizGenerationError(RuntimeError):
    """Quiz generation failed."""


class NoReadyCourseMaterialError(QuizGenerationError, CourseMaterialUnavailableError):
    """The course has no processed material to generate from at all.

    Distinct from a relevance miss: an empty corpus is fixed by uploading and
    processing a document, not by broadening the request.
    """


class InvalidQuizStructureError(QuizGenerationError, InvalidGeneratedStructureError):
    """The provider returned something that is not a valid quiz."""


@dataclass(frozen=True)
class QuizGeneration:
    quiz: QuizGenerationResponse
    material: RetrievedCourseMaterial
    model_used: str
    profile_knowledge: ProfileKnowledgeContext | None = None


def parse_correct_answer(row: QuizQuestion) -> QuizCorrectAnswer | None:
    """Read one question's stored answer document, or ``None`` if unusable.

    A row whose document cannot be read is ungradable rather than fatal, so one
    bad row can never fail a quiz read for every other question in the quiz.
    """
    if row.correct_answer is None:
        return None
    try:
        return _CORRECT_ANSWER_ADAPTER.validate_python(row.correct_answer)
    except ValidationError:
        logger.warning(
            "quiz_questions.correct_answer for row %s is not a valid answer document",
            row.id,
        )
        return None


class QuizService:
    PROMPT_TEMPLATE_NAME = "quiz"
    PROMPT_PATH = Path(__file__).resolve().parents[1] / "app" / "prompts" / "quiz.json"

    @staticmethod
    def get_course_material(
        db: Session, course_id: int, *, query: str
    ) -> RetrievedCourseMaterial:
        return load_retrieved_material(
            db,
            course_id,
            query=query,
            limit=settings.retrieval_chunk_limit,
            min_similarity=settings.retrieval_min_similarity,
            max_characters=settings.quiz_material_max_chars,
        )

    @staticmethod
    def build_retrieval_query(course: Course | None, request: QuizRequest) -> str:
        """Turn a generation request into the query retrieval should rank against."""
        return build_retrieval_query(course, request.topic_focus)

    @classmethod
    def question_types_directive(cls, request: QuizRequest) -> str:
        allowed = ", ".join(
            question_type.value for question_type in request.question_types
        )
        directives = "\n".join(
            f"- {QUESTION_TYPE_DIRECTIVES[question_type]}"
            for question_type in request.question_types
        )
        return (
            f"Use only these question types: {allowed}. Every question's "
            "question_type field must be one of them.\n"
            f"{directives}"
        )

    @classmethod
    def question_schemas(cls, request: QuizRequest) -> str:
        return "\n\n".join(
            QUESTION_TYPE_SCHEMAS[question_type]
            for question_type in request.question_types
        )

    @classmethod
    def build_prompt(cls, course_material: str, request: QuizRequest) -> str:
        return PromptLoader.render(
            cls.PROMPT_TEMPLATE_NAME,
            {
                "QUESTION_COUNT": str(request.question_count),
                "QUESTION_TYPES_DIRECTIVE": cls.question_types_directive(request),
                "QUESTION_SCHEMAS": cls.question_schemas(request),
                "DIFFICULTY_DIRECTIVE": DIFFICULTY_DIRECTIVES[request.difficulty],
                "REQUESTED_DIFFICULTY": request.difficulty.value,
                "TOPIC_FOCUS": request.topic_focus,
                # Rendered last so course material can never forge a placeholder
                # that a later substitution would then fill in.
                "TEXT": course_material,
            },
        )

    @classmethod
    def generate(
        cls,
        db: Session,
        course_id: int,
        request: QuizRequest,
        provider: TextGenerationProvider,
        *,
        user_id: int | None = None,
    ) -> QuizGeneration:
        course = db.get(Course, course_id)

        resolved_user_id = user_id
        if resolved_user_id is None and course is not None:
            resolved_user_id = course.owner_id

        def log_failure(category: ErrorCategory, **extra) -> None:
            if resolved_user_id:
                AiUsageLogger.log_failure(
                    db,
                    user_id=resolved_user_id,
                    course_id=course_id,
                    generation_type=GenerationType.QUIZ,
                    error_category=category,
                    **extra,
                )

        if count_available_chunks(db, course_id) == 0:
            log_failure(ErrorCategory.NO_READY_MATERIAL)
            raise NoReadyCourseMaterialError(NO_READY_MATERIAL_MESSAGE)

        query = cls.build_retrieval_query(course, request)

        try:
            material = cls.get_course_material(db, course_id, query=query)
        except MaterialNotIndexedError:
            log_failure(ErrorCategory.MATERIAL_NOT_INDEXED)
            raise
        except NoRelevantMaterialError:
            log_failure(ErrorCategory.NO_RELEVANT_MATERIAL)
            raise
        except MaterialRetrievalError:
            log_failure(ErrorCategory.RETRIEVAL_ERROR)
            raise

        generation_ctx = assemble_generation_context(
            db,
            course_id=course_id,
            user_id=resolved_user_id,
            course_material=material,
            include_profile_context=request.include_profile_context,
        )

        prompt = cls.build_prompt(generation_ctx.combined_text, request)
        metadata = None

        receipt = None
        if resolved_user_id:
            receipt = CreditService.charge(
                db, resolved_user_id, 1.0, source_type="quiz"
            )
            if receipt is None:
                log_failure(ErrorCategory.INSUFFICIENT_CREDITS)
                raise InsufficientCreditsError("Insufficient credits.")

        try:
            if hasattr(provider, "generate_json_with_metadata"):
                result, metadata = provider.generate_json_with_metadata(prompt)
            else:
                result = provider.generate_json(prompt)
        except TextGenerationError as exc:
            if resolved_user_id:
                CreditService.refund(db, receipt)
            log_failure(getattr(exc, "error_category", ErrorCategory.PROVIDER_ERROR))
            raise QuizGenerationError("Text generation provider failed.") from exc
        except Exception:
            if resolved_user_id:
                CreditService.refund(db, receipt)
            raise

        try:
            validated = QuizGenerationResponse.model_validate(result)
            cls.assert_matches_request(validated, request)
        except (ValidationError, ValueError) as exc:
            if resolved_user_id:
                CreditService.refund(db, receipt)
            log_failure(
                ErrorCategory.INVALID_STRUCTURE,
                latency_ms=metadata.latency_ms if metadata else None,
            )
            raise InvalidQuizStructureError(
                "Generated quiz has an invalid structure."
            ) from exc

        if resolved_user_id:
            AiUsageLogger.log_success(
                db,
                user_id=resolved_user_id,
                course_id=course_id,
                generation_type=GenerationType.QUIZ,
                metadata=metadata,
            )

        return QuizGeneration(
            quiz=validated,
            material=material,
            model_used=model_identifier(metadata),
            profile_knowledge=generation_ctx.profile_knowledge,
        )

    @staticmethod
    def assert_matches_request(
        quiz: QuizGenerationResponse,
        request: QuizRequest,
    ) -> None:
        """Hold the response to the settings that asked for it.

        The prompt states every constraint, but prompt compliance is not a
        guarantee, so the settings a student chose are enforced here as well.
        """
        if len(quiz.questions) != request.question_count:
            raise ValueError(
                "Generated quiz does not contain the requested number of questions."
            )

        allowed = set(request.question_types)
        for question in quiz.questions:
            if question.question_type not in allowed:
                raise ValueError(
                    "Generated quiz uses a question type the request did not allow."
                )
            if question.difficulty is not request.difficulty:
                raise ValueError(
                    "Generated quiz does not use the requested difficulty."
                )

    @staticmethod
    def save_generated_quiz(
        db: Session,
        course_id: int,
        quiz_data: QuizGenerationResponse,
        *,
        user_id: int | None = None,
        model_used: str | None = None,
        generation_settings: str | None = None,
        generation_context: str | None = None,
    ) -> Quiz:
        """Write the quiz and all of its questions in one transaction.

        Nothing here can partially succeed: a failure on any question rolls the
        quiz row back with it, so a quiz never exists with fewer questions than
        it was generated with.
        """
        quiz = Quiz(
            course_id=course_id,
            user_id=user_id,
            title=quiz_data.title,
            model_used=model_used,
            generation_settings=generation_settings,
            generation_context=generation_context,
        )

        try:
            db.add(quiz)
            db.flush()

            for question_index, question in enumerate(quiz_data.questions):
                db.add(
                    QuizQuestion(
                        quiz_id=quiz.id,
                        question_index=question_index,
                        question_type=question.question_type.value,
                        difficulty=question.difficulty.value,
                        question_text=question.question,
                        options=question.stored_options(),
                        correct_option_index=question.stored_option_index(),
                        correct_answer=question.stored_answer().model_dump(mode="json"),
                        topic=question.topic,
                        explanation=question.explanation,
                    )
                )

            db.flush()
            db.commit()
        except Exception:
            db.rollback()
            raise

        db.refresh(quiz)
        return quiz

    @staticmethod
    def list_course_quizzes(db: Session, course_id: int) -> Sequence[tuple[Quiz, int]]:
        """List an already authorized course's quizzes, newest first, with counts."""
        question_count = (
            select(func.count(QuizQuestion.id))
            .where(QuizQuestion.quiz_id == Quiz.id)
            .scalar_subquery()
        )
        rows = db.execute(
            select(Quiz, question_count)
            .where(Quiz.course_id == course_id)
            .order_by(Quiz.created_at.desc(), Quiz.id.desc())
        ).all()
        return [(row[0], row[1]) for row in rows]

    @staticmethod
    def get_course_quiz(db: Session, course_id: int, quiz_id: int) -> Quiz:
        """Load one quiz scoped to its parent course, or deny without disclosure.

        The course predicate lives in the same statement as the identifier, so a
        quiz belonging to another course is indistinguishable from one that does
        not exist.
        """
        quiz = db.scalars(
            select(Quiz)
            .where(Quiz.id == quiz_id, Quiz.course_id == course_id)
            .options(selectinload(Quiz.questions))
        ).one_or_none()

        if quiz is None:
            raise NotFoundException(detail=QUIZ_NOT_FOUND)
        return quiz

    @staticmethod
    def build_question_view(row: QuizQuestion) -> QuizQuestionView:
        return QuizQuestionView(
            question_id=row.id,
            question_number=row.question_index + 1,
            question_type=QuizQuestionType(row.question_type),
            difficulty=QuizDifficulty(row.difficulty) if row.difficulty else None,
            topic=row.topic or "",
            question=row.question_text,
            options=list(row.options) if row.options else None,
            correct_option_index=row.correct_option_index,
            correct_answer=parse_correct_answer(row),
            explanation=row.explanation or "",
        )

    @classmethod
    def build_quiz_view(cls, quiz: Quiz) -> QuizView:
        questions = sorted(quiz.questions, key=lambda row: (row.question_index, row.id))
        return QuizView(
            quiz_id=quiz.id,
            course_id=quiz.course_id,
            title=quiz.title,
            created_at=quiz.created_at,
            user_id=quiz.user_id,
            model_used=quiz.model_used,
            generation_settings=cls._document(quiz, "generation_settings"),
            generation_context=cls._document(quiz, "generation_context"),
            questions=[cls.build_question_view(row) for row in questions],
        )

    @classmethod
    def build_quiz_summary(cls, quiz: Quiz, question_count: int) -> QuizSummary:
        return QuizSummary(
            quiz_id=quiz.id,
            course_id=quiz.course_id,
            title=quiz.title,
            question_count=question_count,
            created_at=quiz.created_at,
            user_id=quiz.user_id,
            model_used=quiz.model_used,
            generation_settings=cls._document(quiz, "generation_settings"),
            generation_context=cls._document(quiz, "generation_context"),
        )

    @staticmethod
    def _document(quiz: Quiz, field: str) -> dict | None:
        return parse_json_object(
            getattr(quiz, field), field=field, table="quizzes", row_id=quiz.id
        )
