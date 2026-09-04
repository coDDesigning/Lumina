"""Fresh questions in the mould of ones this course has already set, as real quizzes.

A student who has worked a past paper can answer it from memory, which is
exactly when it stops teaching them anything. This gives them the same question
again in a form memory does not help with: same skill, same level, same shape,
different specifics.

The originals are the rows extraction already wrote, so the student never
pastes a question and the model is never asked what a past paper contains. That
matters twice: a question nobody can trace back to a real paper is not evidence
of anything, and re-reading the paper would charge for work already done.

The model is shown the originals numbered, and answers with those numbers. It
is never given a row identifier and never asked to echo one back -- the
application resolves each number against the questions it actually supplied,
the same discipline citations use.

What the model returns is the ordinary quiz question contract, and what this
module writes is a row in ``quizzes``. Anything else would need its own
attempts, its own grading, its own mastery, and its own contribution to course
progress, and a student's similar-question work would count for nothing in the
course it belongs to. Reusing the tables is what lets an attempt here move the
topic mastery the next exam plan ranks with.

Two bodies of text reach the prompt and they are kept apart on purpose. The past
questions establish structure, phrasing, depth, and difficulty. The course
material establishes what is true. A past paper can be out of date or simply
wrong, so it is never allowed to settle an answer, and where the two disagree
the material wins.

Validation is all-or-nothing. A set that is one question short, or that copies an
original, or that cites nothing that resolves, is refused whole rather than
persisted partially: a student who paid for five questions and silently received
four has been charged for work that was not done.
"""

import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models import (
    OUTPUT_TYPE_EXAM_SIMILAR_QUESTIONS,
    QUIZ_PURPOSE_EXAM_SIMILAR_QUESTIONS,
    GeneratedOutput,
    PastExamQuestion,
    Quiz,
)
from schemas.ai_usage import GenerationType
from schemas.exam_mode import (
    MAX_SIMILAR_QUESTIONS,
    ExamArtifactGenerationContext,
    ExamSimilarQuestionsSettings,
    GeneratedSimilarQuestionResponse,
    SimilarQuestionDifficultyPolicy,
)
from schemas.prompt_context import PromptContext
from schemas.quiz import QuizGenerationResponse, QuizQuestionType, QuizView
from services.exam_artifacts import (
    ExamArtifactGeneration,
    ExamArtifactService,
    ExamArtifactSpec,
    InvalidExamArtifactStructureError,
    PlannedTopic,
)
from services.exam_quiz import ExamQuizService, topic_past_questions
from services.exam_topics import TOPIC_KEY_VERSION, canonical_topic_key
from services.generated_output import GeneratedOutputService
from services.prompt_loader import PromptLoader
from services.quiz import (
    QUESTION_TYPE_DIRECTIVES,
    QUESTION_TYPE_SCHEMAS,
    QuizService,
)
from services.text_generation import TextGenerationProvider
from utils.ai_errors import ExamAnalysisRequiredError
from utils.exceptions import NotFoundException

logger = logging.getLogger(__name__)

TEMPLATE_NAME = "exam_style_question"
QUERY_SUFFIX = "worked examples methods derivations applications"

PROVIDER_FAILED_MESSAGE = "Text generation provider failed."
INVALID_MESSAGE = "Generated similar questions have an invalid structure."

NO_PAST_QUESTIONS_MESSAGE = (
    "No past exam question was found for this topic. Upload a past paper for "
    "this course, or study this topic with its practice questions instead."
)

MAX_SOURCE_QUESTION_CHARS = 1200

SIMILAR_QUESTION_TYPES = (
    QuizQuestionType.MULTIPLE_CHOICE,
    QuizQuestionType.TRUE_FALSE,
    QuizQuestionType.SHORT_ANSWER,
    QuizQuestionType.OPEN_ENDED,
)

# Above this, two questions are the same question with a word moved. Chosen to
# catch a shuffled restatement while leaving room for a question that shares a
# stem and genuinely changes what is asked.
NEAR_DUPLICATE_RATIO = 0.95

MATCH_SOURCE_DIFFICULTY_DIRECTIVE = (
    "Give each new question the same difficulty as the past question it mirrors. "
    "Do not make it easier to be safe, and do not make it harder to be thorough."
)

_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")


class NoPastQuestionsError(ExamAnalysisRequiredError):
    """There is nothing to write a similar question to.

    A conflict rather than an empty success: silently returning nothing would
    charge a topic's unlock and hand back a page with no explanation of why it
    is blank.
    """


@dataclass(frozen=True)
class PersistedSimilarQuestions:
    """One written question set, its history row, and what it cost."""

    quiz: Quiz
    view: QuizView
    output: GeneratedOutput
    credits_charged: float
    source_question_ids: list[int]


def render_originals(questions: list[PastExamQuestion]) -> str:
    """The originals as the prompt shows them: numbered, verbatim, bounded."""
    lines: list[str] = []
    for number, question in enumerate(questions, start=1):
        text = question.question_text.strip()[:MAX_SOURCE_QUESTION_CHARS]
        marks = f" [{question.marks:g} marks]" if question.marks is not None else ""
        lines.append(f"{number}. {text}{marks}")
    return "\n\n".join(lines)


def normalised_question_text(value: str) -> str:
    """Question text reduced to what a reader would call the same question."""
    return _NON_ALPHANUMERIC.sub(" ", value.strip().lower()).strip()


def _is_near_duplicate(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    return SequenceMatcher(None, left, right).ratio() >= NEAR_DUPLICATE_RATIO


def difficulty_directive(policy: SimilarQuestionDifficultyPolicy) -> str:
    if policy is SimilarQuestionDifficultyPolicy.MATCH_SOURCE:
        return MATCH_SOURCE_DIFFICULTY_DIRECTIVE
    return (
        f'Write every question at "{policy.value}" difficulty, and mark each one '
        f'with a difficulty field of exactly "{policy.value}".'
    )


def _type_block(types: tuple[QuizQuestionType, ...]) -> str:
    return "\n".join(f"- {QUESTION_TYPE_DIRECTIVES[kind]}" for kind in types)


def _schema_block(types: tuple[QuizQuestionType, ...]) -> str:
    return "\n\n".join(QUESTION_TYPE_SCHEMAS[kind] for kind in types)


def _build_prompt(
    material: str,
    topic: PlannedTopic,
    context: PromptContext,
    *,
    originals: str,
    question_count: int,
    policy: SimilarQuestionDifficultyPolicy,
    question_types: tuple[QuizQuestionType, ...],
) -> str:
    return PromptLoader.render(
        TEMPLATE_NAME,
        {
            **context.as_variables(),
            "QUESTION_COUNT": str(question_count),
            "DIFFICULTY_DIRECTIVE": difficulty_directive(policy),
            "QUESTION_TYPE_DIRECTIVES": _type_block(question_types),
            "QUESTION_TYPE_SCHEMAS": _schema_block(question_types),
            "TOPIC_LABEL": topic.display_label,
            "ORIGINAL_QUESTIONS": originals,
            "TEXT": material,
        },
    )


def _spec(
    originals: str,
    *,
    question_count: int,
    policy: SimilarQuestionDifficultyPolicy,
    question_types: tuple[QuizQuestionType, ...],
) -> ExamArtifactSpec:
    return ExamArtifactSpec(
        output_type=OUTPUT_TYPE_EXAM_SIMILAR_QUESTIONS,
        generation_type=GenerationType.EXAM_SIMILAR_QUESTIONS,
        prompt_template=TEMPLATE_NAME,
        response_model=GeneratedSimilarQuestionResponse,
        build_prompt=lambda material, topic, context: _build_prompt(
            material,
            topic,
            context,
            originals=originals,
            question_count=question_count,
            policy=policy,
            question_types=question_types,
        ),
        retrieval_query_suffix=QUERY_SUFFIX,
        material_max_characters=settings.exam_similar_questions_material_max_chars,
        provider_failed_message=PROVIDER_FAILED_MESSAGE,
        invalid_structure_message=INVALID_MESSAGE,
    )


def _reject(reason: str) -> None:
    logger.warning("Similar question set refused: %s", reason)
    raise InvalidExamArtifactStructureError(INVALID_MESSAGE)


def build_quiz_data(
    validated: GeneratedSimilarQuestionResponse,
    *,
    originals: list[PastExamQuestion],
    question_count: int,
    question_types: tuple[QuizQuestionType, ...],
    policy: SimilarQuestionDifficultyPolicy,
    topic: PlannedTopic,
    other_topic_keys: frozenset[str],
    supplied_keys: frozenset[str],
) -> tuple[QuizGenerationResponse, list[int]]:
    """Turn a validated provider response into a storable quiz, or refuse it whole.

    Every check here is a reason the set would not be what the student asked
    for. None of them narrows the set to the questions that happened to pass,
    because a short set silently delivers less than was paid for, and the
    caller's refund path only runs if this raises.
    """
    if len(validated.questions) != question_count:
        _reject(
            f"expected {question_count} questions, received {len(validated.questions)}"
        )

    by_number = {number: row for number, row in enumerate(originals, start=1)}
    allowed = {kind.value for kind in question_types}

    questions = []
    source_ids: list[int] = []
    seen_texts: list[str] = []

    for entry in validated.questions:
        source = by_number.get(entry.source_number)
        if source is None:
            _reject(f"source number {entry.source_number} was never supplied")

        question = entry.question

        if question.question_type.value not in allowed:
            _reject(f"question type {question.question_type.value} was not requested")

        if policy is not SimilarQuestionDifficultyPolicy.MATCH_SOURCE:
            if question.difficulty.value != policy.value:
                _reject(
                    f"difficulty {question.difficulty.value} does not honour the "
                    f"requested {policy.value} policy"
                )
        elif source.difficulty and question.difficulty.value != source.difficulty:
            _reject(
                f"difficulty {question.difficulty.value} does not match the "
                f"source's {source.difficulty}"
            )

        # The model's own label is replaced by the plan's before this is
        # stored, so it is only checked for drift onto a topic the plan lists
        # separately. A question about dynamic programming filed under graph
        # traversal would move the wrong topic's mastery.
        drifted = canonical_topic_key(question.topic)
        if drifted and drifted != topic.topic_key and drifted in other_topic_keys:
            _reject(f"question is about {drifted}, not {topic.topic_key}")

        normalised = normalised_question_text(question.question)
        if _is_near_duplicate(
            normalised, normalised_question_text(source.question_text)
        ):
            _reject("question restates the original it was meant to vary")
        if any(_is_near_duplicate(normalised, seen) for seen in seen_texts):
            _reject("two generated questions ask the same thing")
        seen_texts.append(normalised)

        if not {key for key in question.citations if key in supplied_keys}:
            _reject("question cites nothing that resolves to supplied material")

        questions.append(question)
        source_ids.append(source.id)

    renumbered = [
        question.model_copy(update={"question_number": index})
        for index, question in enumerate(questions, start=1)
    ]
    return (
        QuizGenerationResponse(title=validated.title, questions=renumbered),
        source_ids,
    )


class ExamSimilarQuestionsService:
    @staticmethod
    def source_questions(
        db: Session,
        course_id: int,
        topic: PlannedTopic,
        *,
        requested_ids: list[int] | None = None,
    ) -> list[PastExamQuestion]:
        """The originals to work from, or a refusal that says what to do.

        Resolved from rows this course owns, and checked before anything is
        charged, so a topic this course has never examined costs nothing to ask
        about.

        An explicitly requested identifier must survive every filter the default
        path applies: it has to belong to this course, to a paper the plan's
        source analysis selected, and to this topic. One that does not is
        answered as a missing resource rather than as a different error, because
        distinguishing "not yours" from "does not exist" would tell a caller
        which identifiers exist in other courses.
        """
        questions = topic_past_questions(db, course_id, topic)
        if not questions:
            raise NoPastQuestionsError(NO_PAST_QUESTIONS_MESSAGE)

        if requested_ids is None:
            return questions[:MAX_SIMILAR_QUESTIONS]

        available = {question.id: question for question in questions}
        missing = [
            identifier for identifier in requested_ids if identifier not in available
        ]
        if missing:
            raise NotFoundException(detail="Past exam question not found")
        return [available[identifier] for identifier in requested_ids]

    @classmethod
    def generate(
        cls,
        db: Session,
        course_id: int,
        topic: PlannedTopic,
        provider: TextGenerationProvider,
        *,
        user_id: int,
        originals: list[PastExamQuestion],
        question_count: int,
        policy: SimilarQuestionDifficultyPolicy,
        question_types: tuple[QuizQuestionType, ...],
    ) -> ExamArtifactGeneration:
        return ExamArtifactService.generate(
            db,
            course_id,
            topic,
            provider,
            user_id=user_id,
            spec=_spec(
                render_originals(originals),
                question_count=question_count,
                policy=policy,
                question_types=question_types,
            ),
        )

    @classmethod
    def persist(
        cls,
        db: Session,
        course_id: int,
        generation: ExamArtifactGeneration,
        *,
        user_id: int,
        originals: list[PastExamQuestion],
        question_count: int,
        policy: SimilarQuestionDifficultyPolicy,
        question_types: tuple[QuizQuestionType, ...],
        other_topic_keys: frozenset[str],
        generation_request_id: str | None = None,
    ) -> PersistedSimilarQuestions:
        """Validate the set, then write the quiz and its history row together.

        ``save_generated_quiz`` stages without committing and
        ``GeneratedOutputService.record`` owns the commit, the same arrangement
        ordinary quiz generation uses, so a question set can never exist
        without the history row that explains where it came from.
        """
        topic = generation.topic
        validated: GeneratedSimilarQuestionResponse = generation.validated
        supplied = generation.material.citation_map

        quiz_data, source_ids = build_quiz_data(
            validated,
            originals=originals,
            question_count=question_count,
            question_types=question_types,
            policy=policy,
            topic=topic,
            other_topic_keys=other_topic_keys,
            supplied_keys=frozenset(supplied),
        )

        applied = ExamSimilarQuestionsSettings(
            output_type=OUTPUT_TYPE_EXAM_SIMILAR_QUESTIONS,
            topic_key=topic.topic_key,
            display_label=topic.display_label,
            plan_output_id=topic.plan_output_id,
            analysis_output_id=topic.analysis_output_id,
            document_ids_requested=list(topic.document_ids),
            retrieval_limit=settings.retrieval_chunk_limit,
            retrieval_min_similarity=settings.retrieval_min_similarity,
            material_max_characters=(
                settings.exam_similar_questions_material_max_chars
            ),
            topic_key_version=TOPIC_KEY_VERSION,
            prompt_template=TEMPLATE_NAME,
            prompt_version=generation.prompt_version,
            question_count=question_count,
            question_types=[kind.value for kind in question_types],
            answers_hidden=True,
            source_question_ids=[row.id for row in originals],
            difficulty_policy=policy.value,
        )
        context = ExamArtifactGenerationContext.from_material(generation.material)
        context = context.model_copy(
            update={
                "plan_output_id": topic.plan_output_id,
                "topic_key": topic.topic_key,
            }
        )
        applied_json = applied.model_dump_json()
        context_json = context.model_dump_json()

        try:
            quiz = QuizService.save_generated_quiz(
                db,
                course_id,
                quiz_data,
                user_id=user_id,
                model_used=generation.model_used,
                generation_settings=applied_json,
                generation_context=context_json,
                citations=supplied,
                commit=False,
                record_output=False,
                topic_override=topic.display_label,
                purpose=QUIZ_PURPOSE_EXAM_SIMILAR_QUESTIONS,
                exam_plan_output_id=topic.plan_output_id,
                exam_topic_key=topic.topic_key,
                generation_request_id=generation_request_id,
                source_question_ids=source_ids,
            )
            view = QuizService.build_quiz_view(quiz)
            # An examination's answers do not belong in a history document a
            # reopen can serve, so the stored content is the redacted view.
            output = GeneratedOutputService.record(
                db,
                course_id=course_id,
                user_id=user_id,
                output_type=OUTPUT_TYPE_EXAM_SIMILAR_QUESTIONS,
                content=ExamQuizService.hide_answers(view).model_dump_json(),
                model_used=generation.model_used,
                generation_settings=applied_json,
                generation_context=context_json,
            )
        except Exception:
            db.rollback()
            raise

        db.refresh(quiz)
        return PersistedSimilarQuestions(
            quiz=quiz,
            view=QuizService.build_quiz_view(quiz),
            output=output,
            credits_charged=generation.unlock.amount,
            source_question_ids=source_ids,
        )

    @staticmethod
    def latest(
        db: Session, course_id: int, *, topic_key: str
    ) -> GeneratedOutput | None:
        return ExamArtifactService.latest(
            db, course_id, OUTPUT_TYPE_EXAM_SIMILAR_QUESTIONS, topic_key=topic_key
        )

    @staticmethod
    def latest_quiz(db: Session, course_id: int, *, topic_key: str) -> Quiz | None:
        """The most recent similar-question quiz for one topic.

        Read from ``quizzes`` rather than from the history document, because
        the quiz rows are the assessment and the document is a record of one.
        A set generated before these were quizzes has no row here, which is why
        this returns nothing for it rather than half-parsing an older shape.
        """
        return db.scalars(
            select(Quiz)
            .where(
                Quiz.course_id == course_id,
                Quiz.purpose == QUIZ_PURPOSE_EXAM_SIMILAR_QUESTIONS,
                Quiz.exam_topic_key == topic_key,
            )
            .order_by(Quiz.created_at.desc(), Quiz.id.desc())
            .limit(1)
        ).first()
