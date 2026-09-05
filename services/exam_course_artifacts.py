"""The two things a student makes from their whole plan, not one topic of it.

A mock exam is the paper they sit to find out whether they are ready; a review
sheet is what they read in the last hour. Both draw across every topic the plan
ranked, weighted by that ranking, which is why neither is priced per topic: a
student who has unlocked one topic has not paid for a paper covering twelve.

Weighting is arithmetic over the plan's own order rather than a second opinion.
The plan already decided what matters, deterministically and explainably; asking
a model to decide again would produce a paper whose emphasis nobody could
account for.

A mock exam is a real quiz for the same reason a topic exam is: attempts,
grading, mastery and course progress already exist, and its results should feed
the next plan rather than sit outside the course.
"""

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models import (
    OUTPUT_TYPE_EXAM_MOCK_EXAM,
    OUTPUT_TYPE_EXAM_REVIEW_SHEET,
    QUIZ_PURPOSE_EXAM_MOCK_EXAM,
    GeneratedOutput,
    Quiz,
)
from schemas.ai_usage import GenerationType
from schemas.exam_mode import (
    ExamCourseArtifactContext,
    ExamCourseArtifactSettings,
    ExamReviewSheetDocument,
    GeneratedExamReviewSheet,
)
from schemas.prompt_context import PromptContext
from schemas.quiz import (
    MAX_QUIZ_QUESTIONS,
    MIN_QUIZ_QUESTIONS,
    QuizGenerationResponse,
    QuizQuestionType,
    QuizView,
)
from services.citations import SuppliedCitation, resolve_citations
from services.credits import CreditService
from services.exam_artifacts import (
    ExamArtifactError,
    ExamArtifactGeneration,
    ExamArtifactService,
    ExamArtifactSpec,
    InvalidExamArtifactStructureError,
    PlannedExam,
    PlannedTopic,
)
from services.exam_quiz import (
    NO_PAST_QUESTIONS,
    PAST_QUESTIONS_PREFACE,
    ExamQuizService,
    topic_past_questions,
)
from services.exam_topics import TOPIC_KEY_VERSION, canonical_topic_key
from services.generated_output import GeneratedOutputService
from services.exam_mock_allocation import (
    ALLOCATION_POLICY_VERSION,
    TopicQuota,
    TypeQuota,
    allocate_topic_quota,
    default_question_mix,
    validate_question_mix,
)
from services.prompt_loader import PromptLoader
from services.quiz import (
    QUESTION_TYPE_DIRECTIVES,
    QUESTION_TYPE_SCHEMAS,
    QuizService,
)
from services.text_generation import TextGenerationProvider

logger = logging.getLogger(__name__)

MOCK_TEMPLATE_NAME = "exam_mock_exam"
REVIEW_TEMPLATE_NAME = "exam_review_sheet"

MOCK_PRICE_KEY = "exam_mock_exam"
REVIEW_PRICE_KEY = "exam_review_sheet"

MOCK_QUERY_SUFFIX = "assessed problems derivations applications examination"
REVIEW_QUERY_SUFFIX = "key results definitions procedures common mistakes"

MOCK_QUERY_SUBJECT = "exam revision across every planned topic"
REVIEW_QUERY_SUBJECT = "last minute revision across every planned topic"

PROVIDER_FAILED_MESSAGE = "Text generation provider failed."
MOCK_INVALID_MESSAGE = "Generated mock exam has an invalid structure."
REVIEW_INVALID_MESSAGE = "Generated review sheet has an invalid structure."

# All four storable types. A mock exam is the one artifact a student configures
# the shape of, so restricting the menu here would silently override them.
MOCK_QUESTION_TYPES = (
    QuizQuestionType.MULTIPLE_CHOICE,
    QuizQuestionType.TRUE_FALSE,
    QuizQuestionType.SHORT_ANSWER,
    QuizQuestionType.OPEN_ENDED,
)


class MockExamTopicError(ExamArtifactError):
    """The requested coverage cannot be turned into a paper."""


MAX_STYLE_QUESTIONS = 8
MAX_STYLE_QUESTION_CHARS = 400

MIN_TOPIC_WEIGHT = 1
SECONDS_PER_MINUTE = 60
MAX_REVIEW_TOPICS_SHOWN = 20


@dataclass(frozen=True)
class PersistedMockExam:
    quiz: Quiz
    view: QuizView
    output: GeneratedOutput
    context: ExamCourseArtifactContext
    credits_charged: float


@dataclass(frozen=True)
class PersistedReviewSheet:
    output: GeneratedOutput
    document: ExamReviewSheetDocument
    credits_charged: float


def topic_weights(plan: PlannedExam) -> list[tuple[str, int]]:
    """How much of the paper each topic should take, from the plan's own order.

    A linear taper over rank rather than a share of the priority score. The
    score is a comparison between topics, not a quantity of exam: a topic
    scoring 90 beside one scoring 45 deserves more of the paper, not exactly
    twice as much of it. Every planned topic keeps at least one share, because a
    topic the student chose and the paper never asks about is a topic they were
    told to study for nothing.
    """
    total = len(plan.topics)
    return [
        (topic.display_label, max(MIN_TOPIC_WEIGHT, total - index))
        for index, topic in enumerate(plan.topics)
    ]


def render_plan_topics(plan: PlannedExam) -> str:
    return "\n".join(
        f"- {label} (weight {weight})" for label, weight in topic_weights(plan)
    )


def selected_topics(
    plan: PlannedExam, topic_keys: list[str] | None
) -> tuple[PlannedTopic, ...]:
    """The plan topics a paper should cover, in the plan's own rank order.

    An arbitrary course topic is not accepted here. The plan is what ranked
    these and what the student confirmed, so a key it does not list is a
    request to examine something nobody planned.
    """
    if topic_keys is None:
        return plan.topics

    by_key = {topic.topic_key: topic for topic in plan.topics}
    unknown = [key for key in topic_keys if key not in by_key]
    if unknown:
        raise MockExamTopicError("This plan does not cover every requested topic.")
    requested = set(topic_keys)
    return tuple(topic for topic in plan.topics if topic.topic_key in requested)


def topic_quotas(
    plan: PlannedExam, topic_keys: list[str] | None, question_count: int
) -> tuple[TopicQuota, ...]:
    """Exactly how many questions each covered topic gets.

    Calculated here rather than asked for in the prompt, because a paper whose
    split nobody computed is a paper nobody can check.
    """
    chosen = selected_topics(plan, topic_keys)
    total = len(chosen)
    weighted = [
        (topic.topic_key, topic.display_label, max(MIN_TOPIC_WEIGHT, total - index))
        for index, topic in enumerate(chosen)
    ]
    try:
        return allocate_topic_quota(weighted, question_count)
    except ValueError as exc:
        raise MockExamTopicError(str(exc)) from exc


def type_quotas(
    mix: list[tuple[str, int]] | None, question_count: int
) -> tuple[TypeQuota, ...]:
    try:
        if mix is None:
            return default_question_mix(question_count)
        return validate_question_mix(mix, question_count=question_count)
    except ValueError as exc:
        raise MockExamTopicError(str(exc)) from exc


def render_topic_quotas(quotas: tuple[TopicQuota, ...]) -> str:
    return "\n".join(
        f"- {quota.display_label}: exactly {quota.question_count} "
        f"question{'s' if quota.question_count != 1 else ''}"
        for quota in quotas
    )


def render_type_quotas(quotas: tuple[TypeQuota, ...]) -> str:
    return "\n".join(
        f"- {quota.question_type}: exactly {quota.count} "
        f"question{'s' if quota.count != 1 else ''}"
        for quota in quotas
    )


def _reject_mock(reason: str) -> None:
    logger.warning("Mock exam refused: %s", reason)
    raise InvalidExamArtifactStructureError(MOCK_INVALID_MESSAGE)


def validate_mock_exam(
    quiz_data: QuizGenerationResponse,
    *,
    quotas: tuple[TopicQuota, ...],
    types: tuple[TypeQuota, ...],
    question_count: int,
    supplied_keys: frozenset[str],
) -> None:
    """Refuse a paper that is not the one that was asked for.

    Every check is all-or-nothing. Fourteen valid questions when fifteen were
    requested is not a partial success: the student paid for a paper of a
    stated shape, and quietly handing back a different one spends their credit
    on work that was not done.
    """
    if len(quiz_data.questions) != question_count:
        _reject_mock(
            f"expected {question_count} questions, received {len(quiz_data.questions)}"
        )

    numbers = [question.question_number for question in quiz_data.questions]
    if len(set(numbers)) != len(numbers):
        _reject_mock("two questions share a question number")

    seen: set[str] = set()
    by_topic: dict[str, int] = {}
    by_type: dict[str, int] = {}
    expected_topics = {quota.topic_key: quota.question_count for quota in quotas}

    for question in quiz_data.questions:
        normalised = " ".join(question.question.strip().lower().split())
        if normalised in seen:
            _reject_mock("two questions ask the same thing")
        seen.add(normalised)

        key = canonical_topic_key(question.topic)
        if key not in expected_topics:
            # Never relabelled onto the nearest planned topic: that would give a
            # student mastery for a topic the question did not assess.
            _reject_mock(
                f"question is about {key or 'nothing'}, which was not requested"
            )
        by_topic[key] = by_topic.get(key, 0) + 1
        by_type[question.question_type.value] = (
            by_type.get(question.question_type.value, 0) + 1
        )

        if not {
            candidate for candidate in question.citations if candidate in supplied_keys
        }:
            _reject_mock("question cites nothing that resolves to supplied material")

    for topic_key, expected in expected_topics.items():
        if by_topic.get(topic_key, 0) != expected:
            _reject_mock(
                f"topic {topic_key} has {by_topic.get(topic_key, 0)} questions, "
                f"not the {expected} it was allocated"
            )

    for quota in types:
        if by_type.get(quota.question_type, 0) != quota.count:
            _reject_mock(
                f"type {quota.question_type} has {by_type.get(quota.question_type, 0)} "
                f"questions, not the {quota.count} requested"
            )


def plan_past_question_style(db: Session, course_id: int, plan: PlannedExam) -> str:
    """A sample of this course's own questions across the whole plan.

    Taken evenly from the ranked topics rather than exhausting the first one,
    so the sample shows the paper's range instead of one topic's habits.
    """
    lines: list[str] = []
    for topic in plan.topics:
        for question in topic_past_questions(db, course_id, topic)[:2]:
            text = question.question_text.strip()[:MAX_STYLE_QUESTION_CHARS]
            lines.append(f"- {text}")
            if len(lines) >= MAX_STYLE_QUESTIONS:
                break
        if len(lines) >= MAX_STYLE_QUESTIONS:
            break
    if not lines:
        return NO_PAST_QUESTIONS
    return "\n".join([PAST_QUESTIONS_PREFACE, "", *lines])


def _mock_prompt(
    material: str,
    plan: PlannedExam,
    context: PromptContext,
    *,
    question_count: int,
    style: str,
    quotas: tuple[TopicQuota, ...],
    types: tuple[TypeQuota, ...],
) -> str:
    return PromptLoader.render(
        MOCK_TEMPLATE_NAME,
        {
            **context.as_variables(),
            "QUESTION_COUNT": str(question_count),
            "QUESTION_TYPE_DIRECTIVES": "\n".join(
                f"- {QUESTION_TYPE_DIRECTIVES[kind]}" for kind in MOCK_QUESTION_TYPES
            ),
            "QUESTION_TYPE_QUOTAS": render_type_quotas(types),
            "QUESTION_TYPE_SCHEMAS": "\n\n".join(
                QUESTION_TYPE_SCHEMAS[kind] for kind in MOCK_QUESTION_TYPES
            ),
            "PAST_QUESTION_STYLE": style,
            "PLAN_TOPICS": render_topic_quotas(quotas),
            "TEXT": material,
        },
    )


def _review_prompt(material: str, plan: PlannedExam, context: PromptContext) -> str:
    return PromptLoader.render(
        REVIEW_TEMPLATE_NAME,
        {
            **context.as_variables(),
            "PLAN_TOPICS": render_plan_topics(plan),
            "TEXT": material,
        },
    )


def _mock_spec(
    *,
    question_count: int,
    style: str,
    quotas: tuple[TopicQuota, ...],
    types: tuple[TypeQuota, ...],
) -> ExamArtifactSpec:
    return ExamArtifactSpec(
        output_type=OUTPUT_TYPE_EXAM_MOCK_EXAM,
        generation_type=GenerationType.EXAM_MOCK_EXAM,
        prompt_template=MOCK_TEMPLATE_NAME,
        response_model=QuizGenerationResponse,
        build_prompt=lambda material, plan, context: _mock_prompt(
            material,
            plan,
            context,
            question_count=question_count,
            style=style,
            quotas=quotas,
            types=types,
        ),
        retrieval_query_suffix=MOCK_QUERY_SUFFIX,
        material_max_characters=settings.exam_mock_exam_material_max_chars,
        provider_failed_message=PROVIDER_FAILED_MESSAGE,
        invalid_structure_message=MOCK_INVALID_MESSAGE,
    )


REVIEW_SPEC = ExamArtifactSpec(
    output_type=OUTPUT_TYPE_EXAM_REVIEW_SHEET,
    generation_type=GenerationType.EXAM_REVIEW_SHEET,
    prompt_template=REVIEW_TEMPLATE_NAME,
    response_model=GeneratedExamReviewSheet,
    build_prompt=lambda material, plan, context: _review_prompt(
        material, plan, context
    ),
    retrieval_query_suffix=REVIEW_QUERY_SUFFIX,
    material_max_characters=settings.exam_review_sheet_material_max_chars,
    provider_failed_message=PROVIDER_FAILED_MESSAGE,
    invalid_structure_message=REVIEW_INVALID_MESSAGE,
)


def _settings_document(
    plan: PlannedExam,
    generation: ExamArtifactGeneration,
    *,
    output_type: str,
    template: str,
    max_characters: int,
    question_count: int | None = None,
    answers_hidden: bool = False,
    duration_minutes: int | None = None,
    quotas: tuple[TopicQuota, ...] = (),
    types: tuple[TypeQuota, ...] = (),
) -> ExamCourseArtifactSettings:
    return ExamCourseArtifactSettings(
        output_type=output_type,
        plan_output_id=plan.plan_output_id,
        analysis_output_id=plan.analysis_output_id,
        topic_keys=(
            [quota.topic_key for quota in quotas]
            if quotas
            else [topic.topic_key for topic in plan.topics]
        ),
        document_ids_requested=list(plan.document_ids),
        retrieval_limit=settings.retrieval_chunk_limit,
        retrieval_min_similarity=settings.retrieval_min_similarity,
        material_max_characters=max_characters,
        topic_key_version=TOPIC_KEY_VERSION,
        prompt_template=template,
        prompt_version=generation.prompt_version,
        question_count=question_count,
        answers_hidden=answers_hidden,
        duration_minutes=duration_minutes,
        topic_quotas=[
            {
                "topic_key": quota.topic_key,
                "display_label": quota.display_label,
                "weight": quota.weight,
                "question_count": quota.question_count,
            }
            for quota in quotas
        ],
        question_type_quotas=[
            {"question_type": quota.question_type, "count": quota.count}
            for quota in types
        ],
        allocation_policy_version=ALLOCATION_POLICY_VERSION if quotas else None,
    )


def _context_document(
    plan: PlannedExam, generation: ExamArtifactGeneration
) -> ExamCourseArtifactContext:
    context = ExamCourseArtifactContext.from_material(generation.material)
    return context.model_copy(
        update={
            "plan_output_id": plan.plan_output_id,
            "topic_count": len(plan.topics),
        }
    )


class ExamMockExamService:
    @staticmethod
    def find_by_request_id(
        db: Session,
        course_id: int,
        user_id: int,
        generation_request_id: str | None,
    ) -> PersistedMockExam | None:
        quiz = QuizService.find_by_generation_request_id(
            db,
            course_id=course_id,
            user_id=user_id,
            generation_request_id=generation_request_id,
        )
        if quiz is None:
            return None
        if quiz.purpose != QUIZ_PURPOSE_EXAM_MOCK_EXAM:
            raise RuntimeError(
                "Generation request identifier belongs to another quiz purpose."
            )
        view = QuizService.build_quiz_view(quiz)
        output = GeneratedOutputService.find_quiz_output(
            db,
            course_id=course_id,
            user_id=user_id,
            output_type=OUTPUT_TYPE_EXAM_MOCK_EXAM,
            quiz_id=quiz.id,
        )
        if output is None:
            raise RuntimeError("Mock exam history row is missing.")
        return PersistedMockExam(
            quiz=quiz,
            view=view,
            output=output,
            context=ExamCourseArtifactContext.model_validate(
                view.generation_context or {}
            ),
            credits_charged=0.0,
        )

    @staticmethod
    def resolve_question_count(requested: int | None) -> int:
        if requested is None:
            return settings.exam_mock_exam_question_count
        return max(MIN_QUIZ_QUESTIONS, min(MAX_QUIZ_QUESTIONS, requested))

    @classmethod
    def generate(
        cls,
        db: Session,
        course_id: int,
        plan: PlannedExam,
        provider: TextGenerationProvider,
        *,
        user_id: int,
        question_count: int,
        quotas: tuple[TopicQuota, ...],
        types: tuple[TypeQuota, ...],
    ) -> ExamArtifactGeneration:
        style = plan_past_question_style(db, course_id, plan)
        return ExamArtifactService.generate_for_plan(
            db,
            course_id,
            plan,
            provider,
            user_id=user_id,
            spec=_mock_spec(
                question_count=question_count,
                style=style,
                quotas=quotas,
                types=types,
            ),
            price_key=MOCK_PRICE_KEY,
            query_subject=MOCK_QUERY_SUBJECT,
        )

    @classmethod
    def persist(
        cls,
        db: Session,
        course_id: int,
        generation: ExamArtifactGeneration,
        *,
        user_id: int,
        question_count: int,
        quotas: tuple[TopicQuota, ...],
        types: tuple[TypeQuota, ...],
        duration_minutes: int,
        generation_request_id: str | None = None,
    ) -> PersistedMockExam:
        """Validate the paper against its quotas, then write it.

        Every question keeps the topic label the model chose from the plan's own
        list, because a mock exam spans topics and one override could not be
        right for all of them. A label that is not one of the plan's is refused
        rather than corrected: filing it under the nearest topic would put
        mastery somewhere the student never earned it.

        Validation happens before anything is written, so a paper that does not
        match what was asked for costs nothing and leaves nothing behind.
        """
        plan = generation.plan
        quiz_data: QuizGenerationResponse = generation.validated
        supplied = generation.material.citation_map

        validate_mock_exam(
            quiz_data,
            quotas=quotas,
            types=types,
            question_count=question_count,
            supplied_keys=frozenset(supplied),
        )

        applied = _settings_document(
            plan,
            generation,
            output_type=OUTPUT_TYPE_EXAM_MOCK_EXAM,
            template=MOCK_TEMPLATE_NAME,
            max_characters=settings.exam_mock_exam_material_max_chars,
            question_count=question_count,
            answers_hidden=True,
            duration_minutes=duration_minutes,
            quotas=quotas,
            types=types,
        ).model_dump_json()
        context = _context_document(plan, generation).model_dump_json()

        try:
            quiz = QuizService.save_generated_quiz(
                db,
                course_id,
                quiz_data,
                user_id=user_id,
                model_used=generation.model_used,
                generation_settings=applied,
                generation_context=context,
                citations=supplied,
                commit=False,
                record_output=False,
                purpose=QUIZ_PURPOSE_EXAM_MOCK_EXAM,
                exam_plan_output_id=plan.plan_output_id,
                time_limit_seconds=duration_minutes * SECONDS_PER_MINUTE,
                generation_request_id=generation_request_id,
            )
            output = GeneratedOutputService.record(
                db,
                course_id=course_id,
                user_id=user_id,
                output_type=OUTPUT_TYPE_EXAM_MOCK_EXAM,
                content=ExamQuizService.hide_answers(
                    QuizService.build_quiz_view(quiz)
                ).model_dump_json(),
                model_used=generation.model_used,
                generation_settings=applied,
                generation_context=context,
            )
        except IntegrityError:
            db.rollback()
            existing = cls.find_by_request_id(
                db, course_id, user_id, generation_request_id
            )
            if existing is None:
                raise
            CreditService.refund(db, generation.charge_receipt)
            return existing
        except Exception:
            db.rollback()
            raise

        db.refresh(quiz)
        return PersistedMockExam(
            quiz=quiz,
            view=QuizService.build_quiz_view(quiz),
            output=output,
            context=_context_document(plan, generation),
            credits_charged=generation.credits_charged,
        )

    @staticmethod
    def latest(db: Session, course_id: int) -> GeneratedOutput | None:
        return ExamArtifactService.latest(db, course_id, OUTPUT_TYPE_EXAM_MOCK_EXAM)

    @staticmethod
    def latest_quiz(db: Session, course_id: int) -> Quiz | None:
        """The most recent mock exam paper for one course.

        Read from ``quizzes`` rather than from the history document, because the
        quiz rows are the paper and the document is a record of one.
        """
        return db.scalars(
            select(Quiz)
            .where(
                Quiz.course_id == course_id,
                Quiz.purpose == QUIZ_PURPOSE_EXAM_MOCK_EXAM,
            )
            .order_by(Quiz.created_at.desc(), Quiz.id.desc())
            .limit(1)
        ).first()


class ExamReviewSheetService:
    @classmethod
    def generate(
        cls,
        db: Session,
        course_id: int,
        plan: PlannedExam,
        provider: TextGenerationProvider,
        *,
        user_id: int,
    ) -> ExamArtifactGeneration:
        return ExamArtifactService.generate_for_plan(
            db,
            course_id,
            plan,
            provider,
            user_id=user_id,
            spec=REVIEW_SPEC,
            price_key=REVIEW_PRICE_KEY,
            query_subject=REVIEW_QUERY_SUBJECT,
        )

    @classmethod
    def persist(
        cls,
        db: Session,
        course_id: int,
        generation: ExamArtifactGeneration,
        *,
        user_id: int,
    ) -> PersistedReviewSheet:
        plan = generation.plan
        sheet: GeneratedExamReviewSheet = generation.validated
        supplied = generation.material.citation_map
        known = {topic.display_label: topic.topic_key for topic in plan.topics}

        document = ExamReviewSheetDocument(
            plan_output_id=plan.plan_output_id,
            exam_date=plan.exam_date,
            days_until_exam=plan.days_until_exam,
            title=sheet.title,
            topics=[
                {
                    "topic_key": known.get(
                        entry.topic_label, canonical_topic_key(entry.topic_label)
                    ),
                    "topic_label": entry.topic_label,
                    "must_remember": [
                        _cited(item, supplied) for item in entry.must_remember
                    ],
                    "traps": [_cited(item, supplied) for item in entry.traps],
                }
                for entry in sheet.topics[:MAX_REVIEW_TOPICS_SHOWN]
            ],
            final_checks=[_cited(item, supplied) for item in sheet.final_checks],
            confidence_notes=sheet.confidence_notes,
        )

        output = ExamArtifactService.persist(
            db,
            course_id,
            user_id=user_id,
            output_type=OUTPUT_TYPE_EXAM_REVIEW_SHEET,
            content=document.model_dump_json(),
            model_used=generation.model_used,
            generation_settings=_settings_document(
                plan,
                generation,
                output_type=OUTPUT_TYPE_EXAM_REVIEW_SHEET,
                template=REVIEW_TEMPLATE_NAME,
                max_characters=settings.exam_review_sheet_material_max_chars,
            ).model_dump_json(),
            generation_context=_context_document(plan, generation).model_dump_json(),
        )
        return PersistedReviewSheet(
            output=output,
            document=document,
            credits_charged=generation.credits_charged,
        )

    @staticmethod
    def latest(db: Session, course_id: int) -> GeneratedOutput | None:
        return ExamArtifactService.latest(db, course_id, OUTPUT_TYPE_EXAM_REVIEW_SHEET)


def _cited(value, supplied: dict[str, SuppliedCitation]) -> dict:
    return {
        "text": value.text,
        "citations": [
            citation.model_dump(mode="json")
            for citation in resolve_citations(value.citations, supplied)
        ],
    }
