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
from backend.app.models import (
    Course,
    CourseSettings,
    Quiz,
    QuizAttempt,
    QuizQuestion,
)
from schemas.ai_usage import ErrorCategory, GenerationType
from schemas.citation import Citation
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
from services.citations import SuppliedCitation, resolve_citations
from services.course_material import count_available_chunks
from services.generated_output import GeneratedOutputService
from services.profile_knowledge import (
    ProfileKnowledgeContext,
    assemble_generation_context,
    format_profile_context,
)
from schemas.prompt_context import PromptContext
from services.document_lock import acquire_generation_locks
from services.prompt_context import resolve_prompt_context
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
from services.credits import ChargeReceipt, CreditService, GENERATION_CREDIT_COSTS
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
        '  "explanation": "...",\n'
        '  "citations": ["S1"]\n'
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
        '  "explanation": "...",\n'
        '  "citations": ["S1"]\n'
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
        '  "explanation": "...",\n'
        '  "citations": ["S1"]\n'
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
        '  "explanation": "...",\n'
        '  "citations": ["S1"]\n'
        "}"
    ),
}

DIFFICULTY_DIRECTIVES: dict[QuizDifficulty, str] = {
    QuizDifficulty.EASY: (
        "Every question must be easy: direct recall or straightforward comprehension "
        "of the course material."
    ),
    QuizDifficulty.MEDIUM: (
        "Every question must be of medium difficulty: applying or comparing ideas from "
        "the course material."
    ),
    QuizDifficulty.HARD: (
        "Every question must be hard: analysis, synthesis, or reasoning that combines "
        "several parts of the course material."
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
    effective_request: QuizRequest
    profile_knowledge: ProfileKnowledgeContext | None = None
    charge_receipt: ChargeReceipt | None = None


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


def parse_citations(row: QuizQuestion) -> list[Citation]:
    """Read one question's stored citations, or ``[]`` if unusable.

    A row whose document cannot be read loses its attribution rather than
    failing the quiz read, the same way an unreadable answer document does.
    """
    if not row.citations:
        return []
    try:
        return [Citation.model_validate(item) for item in row.citations]
    except (ValidationError, TypeError):
        logger.warning(
            "quiz_questions.citations for row %s is not a valid citation document",
            row.id,
        )
        return []


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
            include_citations=True,
        )

    @staticmethod
    def credit_cost(request: QuizRequest) -> float:
        if QuizQuestionType.OPEN_ENDED in request.question_types:
            return GENERATION_CREDIT_COSTS["quiz_open_ended"]
        return GENERATION_CREDIT_COSTS["quiz"]

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
    def build_prompt(
        cls,
        course_material: str,
        request: QuizRequest,
        *,
        profile_knowledge: ProfileKnowledgeContext | None = None,
        context: PromptContext,
    ) -> str:
        return PromptLoader.render(
            cls.PROMPT_TEMPLATE_NAME,
            {
                **context.as_variables(),
                "QUESTION_COUNT": str(request.question_count),
                "QUESTION_TYPES_DIRECTIVE": cls.question_types_directive(request),
                "QUESTION_SCHEMAS": cls.question_schemas(request),
                "DIFFICULTY_DIRECTIVE": DIFFICULTY_DIRECTIVES[request.difficulty],
                "REQUESTED_DIFFICULTY": request.difficulty.value,
                "TOPIC_FOCUS": request.topic_focus,
                # Rendered last so course material and profile context can never forge a placeholder
                # that a later substitution would then fill in.
                "TEXT": course_material,
                "PROFILE_CONTEXT": format_profile_context(profile_knowledge),
            },
        )

    @classmethod
    def resolve_effective_request(
        cls, db: Session, course_id: int, request: QuizRequest
    ) -> QuizRequest:
        settings_row = db.scalar(
            select(CourseSettings).where(CourseSettings.course_id == course_id)
        )
        fields_set = request.model_fields_set

        # 1. Question count: explicit request > CourseSettings > default 10
        if "question_count" in fields_set:
            question_count = request.question_count
        elif settings_row is not None and settings_row.question_count is not None:
            question_count = min(max(settings_row.question_count, 1), 20)
        else:
            question_count = request.question_count

        # 2. Difficulty: explicit request > CourseSettings > default MEDIUM
        if "difficulty" in fields_set:
            difficulty = request.difficulty
        elif settings_row is not None and settings_row.difficulty:
            diff_raw = settings_row.difficulty.strip().casefold()
            if diff_raw == "easy":
                difficulty = QuizDifficulty.EASY
            elif diff_raw == "hard":
                difficulty = QuizDifficulty.HARD
            else:
                difficulty = QuizDifficulty.MEDIUM
        else:
            difficulty = request.difficulty

        # 3. Question types: explicit request > default [MULTIPLE_CHOICE]
        if "question_types" in fields_set:
            question_types = request.question_types
        else:
            question_types = request.question_types

        # 4. Topic focus: explicit request > default "All Topics"
        if "topic_focus" in fields_set:
            topic_focus = request.topic_focus
        else:
            topic_focus = request.topic_focus

        return QuizRequest(
            question_count=question_count,
            question_types=question_types,
            difficulty=difficulty,
            topic_focus=topic_focus,
            use_profile_knowledge=request.use_profile_knowledge,
            model=request.model,
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
        effective_request = cls.resolve_effective_request(db, course_id, request)
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
                try:
                    db.commit()
                except Exception:
                    db.rollback()

        if count_available_chunks(db, course_id) == 0:
            log_failure(ErrorCategory.NO_READY_MATERIAL)
            raise NoReadyCourseMaterialError(NO_READY_MATERIAL_MESSAGE)

        query = cls.build_retrieval_query(course, effective_request)

        receipt = None
        if resolved_user_id:
            receipt = CreditService.charge(
                db,
                resolved_user_id,
                cls.credit_cost(effective_request),
                source_type="quiz",
            )
            if receipt is None:
                log_failure(ErrorCategory.INSUFFICIENT_CREDITS)
                raise InsufficientCreditsError("Insufficient credits.")

        try:
            material = cls.get_course_material(db, course_id, query=query)
            with acquire_generation_locks(material.document_ids):
                generation_ctx = assemble_generation_context(
                    db,
                    course_id=course_id,
                    user_id=resolved_user_id,
                    course_material=material,
                    include_profile_context=effective_request.use_profile_knowledge,
                )

                prompt_context = resolve_prompt_context(
                    db, course=course, user_id=resolved_user_id
                )
                prompt = cls.build_prompt(
                    generation_ctx.course_material.text,
                    effective_request,
                    profile_knowledge=generation_ctx.profile_knowledge,
                    context=prompt_context,
                )
                metadata = None

                if hasattr(provider, "generate_json_with_metadata"):
                    result, metadata = provider.generate_json_with_metadata(prompt)
                else:
                    result = provider.generate_json(prompt)
        except MaterialNotIndexedError:
            db.rollback()
            CreditService.refund(db, receipt)
            log_failure(ErrorCategory.MATERIAL_NOT_INDEXED)
            raise
        except NoRelevantMaterialError:
            db.rollback()
            CreditService.refund(db, receipt)
            log_failure(ErrorCategory.NO_RELEVANT_MATERIAL)
            raise
        except MaterialRetrievalError:
            db.rollback()
            CreditService.refund(db, receipt)
            log_failure(ErrorCategory.RETRIEVAL_ERROR)
            raise
        except TextGenerationError as exc:
            db.rollback()
            CreditService.refund(db, receipt)
            log_failure(getattr(exc, "error_category", ErrorCategory.PROVIDER_ERROR))
            raise QuizGenerationError("Text generation provider failed.") from exc
        except Exception:
            db.rollback()
            CreditService.refund(db, receipt)
            raise

        try:
            validated = QuizGenerationResponse.model_validate(result)
            cls.assert_matches_request(validated, effective_request)
        except (ValidationError, ValueError) as exc:
            db.rollback()
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
            effective_request=effective_request,
            profile_knowledge=generation_ctx.profile_knowledge,
            charge_receipt=receipt,
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

    @classmethod
    def save_generated_quiz(
        cls,
        db: Session,
        course_id: int,
        quiz_data: QuizGenerationResponse,
        *,
        user_id: int | None = None,
        model_used: str | None = None,
        generation_settings: str | None = None,
        generation_context: str | None = None,
        citations: dict[str, SuppliedCitation] | None = None,
        record_output: bool = True,
        commit: bool = True,
        topic_override: str | None = None,
        purpose: str | None = None,
        exam_plan_output_id: int | None = None,
        exam_topic_key: str | None = None,
        time_limit_seconds: int | None = None,
        generation_request_id: str | None = None,
        source_question_ids: Sequence[int | None] | None = None,
    ) -> Quiz:
        """Write the quiz, its questions, and its generated output record in one transaction.

        Nothing here can partially succeed: a failure on any question or the
        output record rolls the whole transaction back, so a quiz never exists
        partially persisted without its questions or history row.

        ``topic_override`` replaces the model-supplied ``topic`` on every
        question with one the caller already knows. Ordinary quiz generation
        leaves it alone, because the model's own labels are the only topic
        information a course-wide quiz has. Exam Mode always supplies it: a
        quiz generated for one planned topic must carry that topic's own label,
        or the attempt's mastery would be filed under whatever the model
        happened to write and the next exam plan would count it as unmapped.

        ``record_output`` writes the ``quiz`` history row. Exam Mode turns it
        off and writes its own row under its own output type, so one generated
        quiz never appears twice in a course's history under two names.

        ``source_question_ids`` is positional against ``quiz_data.questions``
        and records which past exam question each one was written in the mould
        of. It exists so a similar-question set can be checked against the
        originals it claims to mirror rather than taken on trust.

        ``time_limit_seconds`` is what makes a quiz a timed one: a positive
        value is the signal the ordinary attempt endpoint uses to insist on a
        server-owned session instead of trusting a clock the client controls.
        """
        quiz = Quiz(
            course_id=course_id,
            user_id=user_id,
            title=quiz_data.title,
            model_used=model_used,
            generation_settings=generation_settings,
            generation_context=generation_context,
            purpose=purpose,
            exam_plan_output_id=exam_plan_output_id,
            exam_topic_key=exam_topic_key,
            time_limit_seconds=time_limit_seconds,
            generation_request_id=generation_request_id,
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
                        topic=topic_override or question.topic,
                        explanation=question.explanation,
                        citations=[
                            citation.model_dump(mode="json")
                            for citation in resolve_citations(
                                question.citations, citations or {}
                            )
                        ],
                        source_past_exam_question_id=(
                            source_question_ids[question_index]
                            if source_question_ids is not None
                            and question_index < len(source_question_ids)
                            else None
                        ),
                    )
                )

            db.flush()
            view = cls.build_quiz_view(quiz)

            persisted_output = None
            if record_output and user_id is not None:
                persisted_output = GeneratedOutputService.record(
                    db,
                    course_id=course_id,
                    user_id=user_id,
                    output_type="quiz",
                    content=view.model_dump_json(),
                    model_used=model_used,
                    generation_settings=generation_settings,
                    generation_context=generation_context,
                    commit=False,
                )

            if commit:
                db.commit()
        except Exception:
            db.rollback()
            raise

        db.refresh(quiz)
        quiz.view = view  # type: ignore[attr-defined]
        quiz.generated_output = persisted_output  # type: ignore[attr-defined]
        return quiz

    @staticmethod
    def list_course_quizzes(
        db: Session, course_id: int
    ) -> Sequence[tuple[Quiz, int, int, float | None, float | None]]:
        """List an already authorized course's quizzes, newest first, with counts and attempt stats."""
        question_count = (
            select(func.count(QuizQuestion.id))
            .where(QuizQuestion.quiz_id == Quiz.id)
            .scalar_subquery()
        )
        attempts_count = (
            select(func.count(QuizAttempt.id))
            .where(QuizAttempt.quiz_id == Quiz.id)
            .scalar_subquery()
        )
        best_score = (
            select(func.max(QuizAttempt.score))
            .where(QuizAttempt.quiz_id == Quiz.id)
            .scalar_subquery()
        )
        last_score = (
            select(QuizAttempt.score)
            .where(QuizAttempt.quiz_id == Quiz.id)
            .order_by(QuizAttempt.created_at.desc(), QuizAttempt.id.desc())
            .limit(1)
            .scalar_subquery()
        )
        rows = db.execute(
            select(Quiz, question_count, attempts_count, best_score, last_score)
            .where(Quiz.course_id == course_id)
            .order_by(Quiz.created_at.desc(), Quiz.id.desc())
        ).all()
        return [(row[0], row[1], row[2], row[3], row[4]) for row in rows]

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
    def hide_answers(view: QuizView) -> QuizView:
        """The same quiz with everything a candidate must not see removed.

        A view-layer redaction only. Grading reads the rows, so nothing
        downstream is affected, and an assessment served through this cannot be
        answered by reading the page that sets it.
        """
        return view.model_copy(
            update={
                "questions": [
                    question.model_copy(
                        update={
                            "correct_answer": None,
                            "correct_option_index": None,
                            "explanation": "",
                        }
                    )
                    for question in view.questions
                ],
                "answers_hidden": True,
            }
        )

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
            citations=parse_citations(row),
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
            quiz_purpose=quiz.purpose,
            exam_plan_output_id=quiz.exam_plan_output_id,
            exam_topic_key=quiz.exam_topic_key,
            timed=bool(quiz.time_limit_seconds),
            time_limit_seconds=quiz.time_limit_seconds,
            questions=[cls.build_question_view(row) for row in questions],
        )

    @classmethod
    def build_quiz_summary(
        cls,
        quiz: Quiz,
        question_count: int,
        *,
        attempts_count: int = 0,
        best_score: float | None = None,
        last_score: float | None = None,
    ) -> QuizSummary:
        return QuizSummary(
            quiz_id=quiz.id,
            course_id=quiz.course_id,
            title=quiz.title,
            question_count=question_count,
            attempts_count=attempts_count,
            best_score=best_score,
            last_score=last_score,
            created_at=quiz.created_at,
            user_id=quiz.user_id,
            model_used=quiz.model_used,
            generation_settings=cls._document(quiz, "generation_settings"),
            generation_context=cls._document(quiz, "generation_context"),
            quiz_purpose=quiz.purpose,
            exam_plan_output_id=quiz.exam_plan_output_id,
            exam_topic_key=quiz.exam_topic_key,
            timed=bool(quiz.time_limit_seconds),
            time_limit_seconds=quiz.time_limit_seconds,
        )

    @staticmethod
    def _document(quiz: Quiz, field: str) -> dict | None:
        return parse_json_object(
            getattr(quiz, field), field=field, table="quizzes", row_id=quiz.id
        )
