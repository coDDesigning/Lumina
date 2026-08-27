"""Fresh questions in the mould of ones this course has already set.

A student who has worked a past paper can answer it from memory, which is
exactly when it stops teaching them anything. This gives them the same question
again in a form memory does not help with: same skill, same level, same shape,
different specifics.

The originals are the rows extraction already wrote, so the student never
pastes a question and the model is never asked what a past paper contains. That
matters twice: a question nobody can trace back to a real paper is not evidence
of anything, and re-reading the paper would charge for work already done.

The model is shown the originals numbered, and answers with those numbers. It
is never given a row identifier and never asked to echo one back — the
application resolves each number against the questions it actually supplied,
the same discipline citations use. A number it invents resolves to nothing and
that question is dropped rather than filed against the wrong original.
"""

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models import (
    OUTPUT_TYPE_EXAM_SIMILAR_QUESTIONS,
    GeneratedOutput,
    PastExamQuestion,
)
from schemas.ai_usage import GenerationType
from schemas.citation import Citation
from schemas.exam_mode import (
    MAX_SIMILAR_QUESTIONS,
    ExamArtifactGenerationContext,
    ExamArtifactGenerationSettings,
    ExamSimilarQuestion,
    ExamSimilarQuestionsDocument,
    GeneratedSimilarQuestions,
)
from schemas.prompt_context import PromptContext
from services.citations import resolve_citations
from services.exam_artifacts import (
    ExamArtifactGeneration,
    ExamArtifactService,
    ExamArtifactSpec,
    PlannedTopic,
)
from services.exam_quiz import topic_past_questions
from services.exam_topics import TOPIC_KEY_VERSION
from services.prompt_loader import PromptLoader
from services.text_generation import TextGenerationProvider
from utils.ai_errors import ExamAnalysisRequiredError

logger = logging.getLogger(__name__)

TEMPLATE_NAME = "exam_similar_questions"
QUERY_SUFFIX = "worked examples methods derivations applications"

PROVIDER_FAILED_MESSAGE = "Text generation provider failed."
INVALID_MESSAGE = "Generated similar questions have an invalid structure."

NO_PAST_QUESTIONS_MESSAGE = (
    "No past exam question was found for this topic. Upload a past paper for "
    "this course, or study this topic with its practice questions instead."
)

MAX_SOURCE_QUESTION_CHARS = 1200


class NoPastQuestionsError(ExamAnalysisRequiredError):
    """There is nothing to write a similar question to.

    A conflict rather than an empty success: silently returning nothing would
    charge a topic's unlock and hand back a page with no explanation of why it
    is blank.
    """


@dataclass(frozen=True)
class PersistedSimilarQuestions:
    output: GeneratedOutput
    document: ExamSimilarQuestionsDocument
    credits_charged: float


def render_originals(questions: list[PastExamQuestion]) -> str:
    """The originals as the prompt shows them: numbered, verbatim, bounded."""
    lines: list[str] = []
    for number, question in enumerate(questions, start=1):
        text = question.question_text.strip()[:MAX_SOURCE_QUESTION_CHARS]
        marks = f" [{question.marks:g} marks]" if question.marks is not None else ""
        lines.append(f"{number}. {text}{marks}")
    return "\n\n".join(lines)


def _build_prompt(
    material: str,
    topic: PlannedTopic,
    context: PromptContext,
    *,
    originals: str,
) -> str:
    return PromptLoader.render(
        TEMPLATE_NAME,
        {
            **context.as_variables(),
            # Rendered last so a placeholder appearing inside the topic label,
            # an original question, or the course material can never be filled
            # in by a later substitution.
            "TOPIC_LABEL": topic.display_label,
            "ORIGINAL_QUESTIONS": originals,
            "TEXT": material,
        },
    )


def _spec(originals: str) -> ExamArtifactSpec:
    return ExamArtifactSpec(
        output_type=OUTPUT_TYPE_EXAM_SIMILAR_QUESTIONS,
        generation_type=GenerationType.EXAM_SIMILAR_QUESTIONS,
        prompt_template=TEMPLATE_NAME,
        response_model=GeneratedSimilarQuestions,
        build_prompt=lambda material, topic, context: _build_prompt(
            material, topic, context, originals=originals
        ),
        retrieval_query_suffix=QUERY_SUFFIX,
        material_max_characters=settings.exam_similar_questions_material_max_chars,
        provider_failed_message=PROVIDER_FAILED_MESSAGE,
        invalid_structure_message=INVALID_MESSAGE,
    )


class ExamSimilarQuestionsService:
    @staticmethod
    def source_questions(
        db: Session, course_id: int, topic: PlannedTopic
    ) -> list[PastExamQuestion]:
        """The originals to work from, or a refusal that says what to do.

        Checked before anything is charged, so a topic this course has never
        examined costs nothing to ask about.
        """
        questions = topic_past_questions(db, course_id, topic)
        if not questions:
            raise NoPastQuestionsError(NO_PAST_QUESTIONS_MESSAGE)
        return questions[:MAX_SIMILAR_QUESTIONS]

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
    ) -> ExamArtifactGeneration:
        return ExamArtifactService.generate(
            db,
            course_id,
            topic,
            provider,
            user_id=user_id,
            spec=_spec(render_originals(originals)),
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
    ) -> PersistedSimilarQuestions:
        topic = generation.topic
        validated: GeneratedSimilarQuestions = generation.validated
        supplied = generation.material.citation_map
        by_number = {number: row for number, row in enumerate(originals, start=1)}

        pairs: list[ExamSimilarQuestion] = []
        for question in validated.questions:
            source = by_number.get(question.source_number)
            if source is None:
                # A number nobody was shown cannot be traced to a paper, so the
                # pair it claims does not exist. Dropped rather than guessed at.
                continue
            pairs.append(
                ExamSimilarQuestion(
                    source_question_id=source.id,
                    source_question_text=source.question_text,
                    source_page_start=source.page_start,
                    source_page_end=source.page_end,
                    question_text=question.question_text.strip(),
                    reference_answer=question.reference_answer.strip(),
                    what_changed=question.what_changed,
                    difficulty=question.difficulty.value,
                    citations=[
                        Citation.model_validate(citation.model_dump(mode="json"))
                        for citation in resolve_citations(question.citations, supplied)
                    ],
                )
            )

        document = ExamSimilarQuestionsDocument(
            topic_key=topic.topic_key,
            display_label=topic.display_label,
            plan_output_id=topic.plan_output_id,
            source_question_ids=[row.id for row in originals],
            questions=pairs,
            confidence_notes=validated.confidence_notes,
        )

        applied = ExamArtifactGenerationSettings(
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
        )
        context = ExamArtifactGenerationContext.from_material(generation.material)
        context = context.model_copy(
            update={
                "plan_output_id": topic.plan_output_id,
                "topic_key": topic.topic_key,
            }
        )

        output = ExamArtifactService.persist(
            db,
            course_id,
            user_id=user_id,
            output_type=OUTPUT_TYPE_EXAM_SIMILAR_QUESTIONS,
            content=document.model_dump_json(),
            model_used=generation.model_used,
            generation_settings=applied.model_dump_json(),
            generation_context=context.model_dump_json(),
        )
        return PersistedSimilarQuestions(
            output=output,
            document=document,
            credits_charged=generation.unlock.amount,
        )

    @staticmethod
    def latest(
        db: Session, course_id: int, *, topic_key: str
    ) -> GeneratedOutput | None:
        return ExamArtifactService.latest(
            db, course_id, OUTPUT_TYPE_EXAM_SIMILAR_QUESTIONS, topic_key=topic_key
        )
