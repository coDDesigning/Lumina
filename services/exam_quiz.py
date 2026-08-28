"""Practice questions and topic examinations, as real quizzes.

These are rows in ``quizzes``, not a parallel world. Anything else would need
its own attempts, its own grading, its own mastery, and its own contribution to
course progress — four copies of working machinery, and a student's exam-mode
work would count for nothing in the course it belongs to. Reusing the tables
means an attempt on a topic exam moves that topic's mastery, and that mastery
is one of the four signals the next exam plan ranks with.

That loop only closes because every question is written with the plan's own
label for the topic. ``quiz_questions.topic`` is otherwise whatever the model
chose to call it, and mastery recorded under a label no plan recognises is
mastery the ranking counts as unmapped. ``topic_override`` is what makes an
exam-mode attempt visible to the next plan.

Practice and examination differ in two ways that matter. Practice shows its
answers, because immediate feedback is the point; an examination hides them
until the attempt is submitted. And an examination is written in the style the
course's own past papers imply, from questions already extracted from them,
without copying one.
"""

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models import (
    OUTPUT_TYPE_EXAM_TOPIC_EXAM,
    OUTPUT_TYPE_EXAM_TOPIC_PRACTICE,
    QUIZ_PURPOSE_EXAM_TOPIC_EXAM,
    QUIZ_PURPOSE_EXAM_TOPIC_PRACTICE,
    GeneratedOutput,
    PastExamQuestion,
    Quiz,
)
from schemas.ai_usage import GenerationType
from schemas.exam_mode import (
    ExamArtifactGenerationContext,
    ExamQuizGenerationSettings,
)
from schemas.prompt_context import PromptContext
from schemas.quiz import (
    MAX_QUIZ_QUESTIONS,
    MIN_QUIZ_QUESTIONS,
    QuizGenerationResponse,
    QuizQuestionType,
    QuizView,
)
from services.exam_artifacts import (
    ExamArtifactGeneration,
    ExamArtifactService,
    ExamArtifactSpec,
    PlannedTopic,
)
from services.exam_question_extraction import PastExamExtractionService
from services.exam_source_analysis import ExamSourceAnalysisService
from services.exam_topics import TOPIC_KEY_VERSION, match_topic_key
from services.generated_output import GeneratedOutputService
from services.prompt_loader import PromptLoader
from services.quiz import (
    QUESTION_TYPE_DIRECTIVES,
    QUESTION_TYPE_SCHEMAS,
    QuizService,
)
from services.text_generation import TextGenerationProvider

logger = logging.getLogger(__name__)

PRACTICE_TEMPLATE_NAME = "exam_topic_practice"
EXAM_TEMPLATE_NAME = "exam_topic_exam"

PRACTICE_QUERY_SUFFIX = "worked examples definitions applications practice"
EXAM_QUERY_SUFFIX = "assessed problems derivations applications examination"

PROVIDER_FAILED_MESSAGE = "Text generation provider failed."
PRACTICE_INVALID_MESSAGE = "Generated practice questions have an invalid structure."
EXAM_INVALID_MESSAGE = "Generated topic exam has an invalid structure."

PRACTICE_TYPES = (
    QuizQuestionType.MULTIPLE_CHOICE,
    QuizQuestionType.TRUE_FALSE,
    QuizQuestionType.SHORT_ANSWER,
)
EXAM_TYPES = (
    QuizQuestionType.MULTIPLE_CHOICE,
    QuizQuestionType.SHORT_ANSWER,
    QuizQuestionType.OPEN_ENDED,
)

MIXED_DIFFICULTY_DIRECTIVE = (
    "Pitch the questions across the whole range: some easy, most of medium "
    "difficulty, and at least one hard. Mark each question with the difficulty "
    "you actually wrote it at, not the one you were aiming for."
)

NO_PAST_QUESTIONS = (
    "This course supplied no past examination paper for this topic, so there is "
    "no house style to follow. Write the examination the way this subject is "
    "ordinarily examined at the stated education level."
)
PAST_QUESTIONS_PREFACE = (
    "These questions were printed in this course's own past papers. Follow their "
    "form, their level, and the kind of work they ask for. Do not reuse one."
)

MAX_STYLE_QUESTIONS = 6
MAX_STYLE_QUESTION_CHARS = 600


@dataclass(frozen=True)
class ExamQuizKind:
    """One of the two quiz-backed artifacts, and everything it decides."""

    output_type: str
    purpose: str
    template: str
    question_types: tuple[QuizQuestionType, ...]
    hide_answers: bool


PRACTICE = ExamQuizKind(
    output_type=OUTPUT_TYPE_EXAM_TOPIC_PRACTICE,
    purpose=QUIZ_PURPOSE_EXAM_TOPIC_PRACTICE,
    template=PRACTICE_TEMPLATE_NAME,
    question_types=PRACTICE_TYPES,
    hide_answers=False,
)
EXAM = ExamQuizKind(
    output_type=OUTPUT_TYPE_EXAM_TOPIC_EXAM,
    purpose=QUIZ_PURPOSE_EXAM_TOPIC_EXAM,
    template=EXAM_TEMPLATE_NAME,
    question_types=EXAM_TYPES,
    hide_answers=True,
)

KINDS = {kind.output_type: kind for kind in (PRACTICE, EXAM)}


@dataclass(frozen=True)
class PersistedExamQuiz:
    """One written quiz, its generated-output row, and what it cost."""

    quiz: Quiz
    view: QuizView
    output: GeneratedOutput
    credits_charged: float


def _type_block(types: tuple[QuizQuestionType, ...]) -> str:
    return "\n".join(f"- {QUESTION_TYPE_DIRECTIVES[kind]}" for kind in types)


def _schema_block(types: tuple[QuizQuestionType, ...]) -> str:
    return "\n\n".join(QUESTION_TYPE_SCHEMAS[kind] for kind in types)


def topic_past_questions(
    db: Session, course_id: int, topic: PlannedTopic
) -> list[PastExamQuestion]:
    """This course's own past questions on one planned topic.

    Read from the rows extraction already wrote, so nothing here reaches a
    provider. The extractor keyed each question by whatever the paper made it
    call the topic, which is rarely the wording the plan chose, so the plan's
    key is matched through ``match_topic_key`` rather than compared directly.
    """
    papers = ExamSourceAnalysisService.past_exam_document_ids(
        db, course_id, topic.retrieval_scope
    )
    if not papers:
        return []

    questions, _ = PastExamExtractionService.load_questions(db, course_id, papers)
    index = {topic.topic_key: topic.topic_key}
    return [
        question
        for question in questions
        if _matches_topic(question, topic.topic_key, index)
    ]


def past_question_style(db: Session, course_id: int, topic: PlannedTopic) -> str:
    """A few of this course's own questions on this topic, verbatim.

    A course with no past paper gets an honest statement that there is no house
    style, rather than an invented one.
    """
    matched = topic_past_questions(db, course_id, topic)
    if not matched:
        return NO_PAST_QUESTIONS

    lines = [PAST_QUESTIONS_PREFACE, ""]
    for question in matched[:MAX_STYLE_QUESTIONS]:
        text = question.question_text.strip()[:MAX_STYLE_QUESTION_CHARS]
        marks = f" [{question.marks:g} marks]" if question.marks is not None else ""
        lines.append(f"- {text}{marks}")
    return "\n".join(lines)


def _matches_topic(
    question: PastExamQuestion, topic_key: str, index: dict[str, str]
) -> bool:
    if question.topic_key == topic_key:
        return True
    for mapping in question.topic_mappings or []:
        if not isinstance(mapping, dict):
            continue
        if mapping.get("topic_key") == topic_key:
            return True
        if match_topic_key(mapping.get("display_label"), index) == topic_key:
            return True
    return False


def _build_prompt(
    kind: ExamQuizKind,
    material: str,
    topic: PlannedTopic,
    context: PromptContext,
    *,
    question_count: int,
    style: str | None,
) -> str:
    variables = {
        **context.as_variables(),
        "QUESTION_COUNT": str(question_count),
        "DIFFICULTY_DIRECTIVE": MIXED_DIFFICULTY_DIRECTIVE,
        "QUESTION_TYPE_DIRECTIVES": _type_block(kind.question_types),
        "QUESTION_TYPE_SCHEMAS": _schema_block(kind.question_types),
    }
    if style is not None:
        variables["PAST_QUESTION_STYLE"] = style
    # Rendered last so a placeholder appearing inside the topic label, the past
    # questions, or the course material can never be filled in by a later
    # substitution.
    variables["TOPIC_LABEL"] = topic.display_label
    variables["TEXT"] = material
    return PromptLoader.render(kind.template, variables)


def _spec(
    kind: ExamQuizKind, *, question_count: int, style: str | None
) -> ExamArtifactSpec:
    return ExamArtifactSpec(
        output_type=kind.output_type,
        generation_type=(
            GenerationType.EXAM_TOPIC_PRACTICE
            if kind is PRACTICE
            else GenerationType.EXAM_TOPIC_EXAM
        ),
        prompt_template=kind.template,
        # Reusing the quiz contract is what keeps an exam-mode question storable
        # and gradable: a type outside the four it allows is refused here rather
        # than discovered by a CHECK constraint at write time.
        response_model=QuizGenerationResponse,
        build_prompt=lambda material, topic, context: _build_prompt(
            kind,
            material,
            topic,
            context,
            question_count=question_count,
            style=style,
        ),
        retrieval_query_suffix=(
            PRACTICE_QUERY_SUFFIX if kind is PRACTICE else EXAM_QUERY_SUFFIX
        ),
        material_max_characters=settings.exam_topic_quiz_material_max_chars,
        provider_failed_message=PROVIDER_FAILED_MESSAGE,
        invalid_structure_message=(
            PRACTICE_INVALID_MESSAGE if kind is PRACTICE else EXAM_INVALID_MESSAGE
        ),
    )


class ExamQuizService:
    @staticmethod
    def resolve_question_count(requested: int | None) -> int:
        if requested is None:
            return settings.exam_quiz_default_question_count
        return max(MIN_QUIZ_QUESTIONS, min(MAX_QUIZ_QUESTIONS, requested))

    @classmethod
    def generate(
        cls,
        db: Session,
        course_id: int,
        topic: PlannedTopic,
        provider: TextGenerationProvider,
        *,
        user_id: int,
        output_type: str,
        question_count: int,
    ) -> ExamArtifactGeneration:
        kind = KINDS[output_type]
        style = past_question_style(db, course_id, topic) if kind.hide_answers else None
        return ExamArtifactService.generate(
            db,
            course_id,
            topic,
            provider,
            user_id=user_id,
            spec=_spec(kind, question_count=question_count, style=style),
        )

    @classmethod
    def persist(
        cls,
        db: Session,
        course_id: int,
        generation: ExamArtifactGeneration,
        *,
        user_id: int,
        output_type: str,
        question_count: int,
    ) -> PersistedExamQuiz:
        """Write the quiz, its questions, and the generated-output row together.

        ``save_generated_quiz`` stages without committing and
        ``GeneratedOutputService.record`` owns the commit, the same arrangement
        ordinary quiz generation uses, so a quiz can never exist without the
        history row that explains where it came from.
        """
        kind = KINDS[output_type]
        topic = generation.topic
        quiz_data: QuizGenerationResponse = generation.validated

        applied = ExamQuizGenerationSettings(
            output_type=output_type,
            topic_key=topic.topic_key,
            display_label=topic.display_label,
            plan_output_id=topic.plan_output_id,
            analysis_output_id=topic.analysis_output_id,
            document_ids_requested=list(topic.document_ids),
            retrieval_limit=settings.retrieval_chunk_limit,
            retrieval_min_similarity=settings.retrieval_min_similarity,
            material_max_characters=settings.exam_topic_quiz_material_max_chars,
            topic_key_version=TOPIC_KEY_VERSION,
            prompt_template=kind.template,
            prompt_version=generation.prompt_version,
            question_count=question_count,
            question_types=list(kind.question_types),
            answers_hidden=kind.hide_answers,
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
                citations=generation.material.citation_map,
                commit=False,
                # Exam Mode writes its own history row under its own output
                # type, so the generic "quiz" one would be a duplicate.
                record_output=False,
                # The single load-bearing line of this module. Without it the
                # attempt's mastery is filed under the model's own wording and
                # the next exam plan counts it as unmapped.
                topic_override=topic.display_label,
                purpose=kind.purpose,
                exam_plan_output_id=topic.plan_output_id,
                exam_topic_key=topic.topic_key,
            )
            view = QuizService.build_quiz_view(quiz)
            output = GeneratedOutputService.record(
                db,
                course_id=course_id,
                user_id=user_id,
                output_type=output_type,
                content=view.model_dump_json(),
                model_used=generation.model_used,
                generation_settings=applied_json,
                generation_context=context_json,
            )
        except Exception:
            db.rollback()
            raise

        db.refresh(quiz)
        return PersistedExamQuiz(
            quiz=quiz,
            view=QuizService.build_quiz_view(quiz),
            output=output,
            credits_charged=generation.unlock.amount,
        )

    @staticmethod
    def hide_answers(view: QuizView) -> QuizView:
        """The same quiz with everything a candidate must not see removed.

        Exam Mode serves its examinations through this rather than through the
        ordinary quiz read, which always exposes the answer: an examination a
        student can read the answers to is not an examination. Grading reads
        the rows, not this view, so nothing downstream is affected.
        """
        return QuizService.hide_answers(view)

    @staticmethod
    def latest(
        db: Session, course_id: int, output_type: str, *, topic_key: str
    ) -> GeneratedOutput | None:
        return ExamArtifactService.latest(
            db, course_id, output_type, topic_key=topic_key
        )
