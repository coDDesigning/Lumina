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
from services.exam_artifacts import (
    ExamArtifactGeneration,
    ExamArtifactService,
    ExamArtifactSpec,
    PlannedExam,
)
from services.exam_quiz import (
    NO_PAST_QUESTIONS,
    PAST_QUESTIONS_PREFACE,
    topic_past_questions,
)
from services.exam_topics import TOPIC_KEY_VERSION, canonical_topic_key
from services.generated_output import GeneratedOutputService
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

MOCK_QUESTION_TYPES = (
    QuizQuestionType.MULTIPLE_CHOICE,
    QuizQuestionType.SHORT_ANSWER,
    QuizQuestionType.OPEN_ENDED,
)

MAX_STYLE_QUESTIONS = 8
MAX_STYLE_QUESTION_CHARS = 400

MIN_TOPIC_WEIGHT = 1
MAX_REVIEW_TOPICS_SHOWN = 20


@dataclass(frozen=True)
class PersistedMockExam:
    quiz: Quiz
    view: QuizView
    output: GeneratedOutput
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
) -> str:
    return PromptLoader.render(
        MOCK_TEMPLATE_NAME,
        {
            **context.as_variables(),
            "QUESTION_COUNT": str(question_count),
            "QUESTION_TYPE_DIRECTIVES": "\n".join(
                f"- {QUESTION_TYPE_DIRECTIVES[kind]}" for kind in MOCK_QUESTION_TYPES
            ),
            "QUESTION_TYPE_SCHEMAS": "\n\n".join(
                QUESTION_TYPE_SCHEMAS[kind] for kind in MOCK_QUESTION_TYPES
            ),
            # Rendered last so a placeholder appearing inside a topic label, a
            # past question, or the course material can never be filled in by a
            # later substitution.
            "PAST_QUESTION_STYLE": style,
            "PLAN_TOPICS": render_plan_topics(plan),
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


def _mock_spec(*, question_count: int, style: str) -> ExamArtifactSpec:
    return ExamArtifactSpec(
        output_type=OUTPUT_TYPE_EXAM_MOCK_EXAM,
        generation_type=GenerationType.EXAM_MOCK_EXAM,
        prompt_template=MOCK_TEMPLATE_NAME,
        response_model=QuizGenerationResponse,
        build_prompt=lambda material, plan, context: _mock_prompt(
            material, plan, context, question_count=question_count, style=style
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
) -> ExamCourseArtifactSettings:
    return ExamCourseArtifactSettings(
        output_type=output_type,
        plan_output_id=plan.plan_output_id,
        analysis_output_id=plan.analysis_output_id,
        topic_keys=[topic.topic_key for topic in plan.topics],
        document_ids_requested=list(plan.document_ids),
        retrieval_limit=settings.retrieval_chunk_limit,
        retrieval_min_similarity=settings.retrieval_min_similarity,
        material_max_characters=max_characters,
        topic_key_version=TOPIC_KEY_VERSION,
        prompt_template=template,
        prompt_version=generation.prompt_version,
        question_count=question_count,
        answers_hidden=answers_hidden,
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
    ) -> ExamArtifactGeneration:
        style = plan_past_question_style(db, course_id, plan)
        return ExamArtifactService.generate_for_plan(
            db,
            course_id,
            plan,
            provider,
            user_id=user_id,
            spec=_mock_spec(question_count=question_count, style=style),
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
    ) -> PersistedMockExam:
        """Write the paper, its questions, and the row that explains them.

        Every question keeps the topic label the model chose from the plan's own
        list, because a mock exam spans topics and one override could not be
        right for all of them. A label that is not one of the plan's is left
        alone rather than corrected: filing it under the nearest topic would put
        mastery somewhere the student never earned it.
        """
        plan = generation.plan
        quiz_data: QuizGenerationResponse = generation.validated
        applied = _settings_document(
            plan,
            generation,
            output_type=OUTPUT_TYPE_EXAM_MOCK_EXAM,
            template=MOCK_TEMPLATE_NAME,
            max_characters=settings.exam_mock_exam_material_max_chars,
            question_count=question_count,
            answers_hidden=True,
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
                citations=generation.material.citation_map,
                commit=False,
                record_output=False,
                purpose=QUIZ_PURPOSE_EXAM_MOCK_EXAM,
                exam_plan_output_id=plan.plan_output_id,
            )
            output = GeneratedOutputService.record(
                db,
                course_id=course_id,
                user_id=user_id,
                output_type=OUTPUT_TYPE_EXAM_MOCK_EXAM,
                content=QuizService.build_quiz_view(quiz).model_dump_json(),
                model_used=generation.model_used,
                generation_settings=applied,
                generation_context=context,
            )
        except Exception:
            db.rollback()
            raise

        db.refresh(quiz)
        return PersistedMockExam(
            quiz=quiz,
            view=QuizService.build_quiz_view(quiz),
            output=output,
            credits_charged=generation.credits_charged,
        )

    @staticmethod
    def latest(db: Session, course_id: int) -> GeneratedOutput | None:
        return ExamArtifactService.latest(db, course_id, OUTPUT_TYPE_EXAM_MOCK_EXAM)


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
