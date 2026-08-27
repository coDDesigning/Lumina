"""The two things a student reads about one topic: the guide and the summary.

Both climb the same ladder in ``services/exam_artifacts.py``; all that lives
here is what makes them different — their prompts, their response models, and
the shape of the document each writes. They are siblings on purpose: the guide
is what you study from, the summary is what you reread, and they are generated
separately so a student can have one without paying for the depth of the other.

They cost nothing beyond their topic's unlock, which is the whole point of
charging per topic: a student who has read the guide can ask for the summary
without thinking about the price.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models import (
    OUTPUT_TYPE_EXAM_TOPIC_GUIDE,
    OUTPUT_TYPE_EXAM_TOPIC_SUMMARY,
    GeneratedOutput,
)
from schemas.ai_usage import GenerationType
from schemas.citation import Citation
from schemas.exam_mode import (
    ExamArtifactGenerationContext,
    ExamArtifactGenerationSettings,
    ExamTopicGuideDocument,
    ExamTopicSummaryDocument,
    GeneratedExamTopicGuide,
    GeneratedExamTopicSummary,
)
from schemas.prompt_context import PromptContext
from services.citations import SuppliedCitation, resolve_citations
from services.exam_artifacts import (
    ExamArtifactGeneration,
    ExamArtifactService,
    ExamArtifactSpec,
    PlannedTopic,
)
from services.exam_topics import TOPIC_KEY_VERSION
from services.prompt_loader import PromptLoader
from services.text_generation import TextGenerationProvider

GUIDE_TEMPLATE_NAME = "exam_topic_guide"
SUMMARY_TEMPLATE_NAME = "exam_topic_summary"

GUIDE_QUERY_SUFFIX = "explanation worked examples definitions common mistakes"
SUMMARY_QUERY_SUFFIX = "key results definitions procedures summary"

HIGH_PRIORITY_NOTE = (
    "The student marked this topic as a priority. Write it in full; do not "
    "shorten it because other topics are waiting."
)
ORDINARY_PRIORITY_NOTE = (
    "The student is studying this topic among several. Cover it completely and "
    "without padding."
)

PROVIDER_FAILED_MESSAGE = "Text generation provider failed."
GUIDE_INVALID_MESSAGE = "Generated topic guide has an invalid structure."
SUMMARY_INVALID_MESSAGE = "Generated topic summary has an invalid structure."


@dataclass(frozen=True)
class PersistedArtifact:
    """One written artifact and what it cost the student."""

    output: GeneratedOutput
    document: ExamTopicGuideDocument | ExamTopicSummaryDocument
    credits_charged: float


def _priority_note(topic: PlannedTopic) -> str:
    return HIGH_PRIORITY_NOTE if topic.is_high_priority else ORDINARY_PRIORITY_NOTE


def _render(
    template: str, material: str, topic: PlannedTopic, context: PromptContext
) -> str:
    return PromptLoader.render(
        template,
        {
            **context.as_variables(),
            "PRIORITY_NOTE": _priority_note(topic),
            # Rendered last so a placeholder appearing inside the topic label
            # or the course material can never be filled in by a later
            # substitution.
            "TOPIC_LABEL": topic.display_label,
            "TEXT": material,
        },
    )


GUIDE_SPEC = ExamArtifactSpec(
    output_type=OUTPUT_TYPE_EXAM_TOPIC_GUIDE,
    generation_type=GenerationType.EXAM_TOPIC_GUIDE,
    prompt_template=GUIDE_TEMPLATE_NAME,
    response_model=GeneratedExamTopicGuide,
    build_prompt=lambda material, topic, context: _render(
        GUIDE_TEMPLATE_NAME, material, topic, context
    ),
    retrieval_query_suffix=GUIDE_QUERY_SUFFIX,
    material_max_characters=settings.exam_topic_guide_material_max_chars,
    provider_failed_message=PROVIDER_FAILED_MESSAGE,
    invalid_structure_message=GUIDE_INVALID_MESSAGE,
)

SUMMARY_SPEC = ExamArtifactSpec(
    output_type=OUTPUT_TYPE_EXAM_TOPIC_SUMMARY,
    generation_type=GenerationType.EXAM_TOPIC_SUMMARY,
    prompt_template=SUMMARY_TEMPLATE_NAME,
    response_model=GeneratedExamTopicSummary,
    build_prompt=lambda material, topic, context: _render(
        SUMMARY_TEMPLATE_NAME, material, topic, context
    ),
    retrieval_query_suffix=SUMMARY_QUERY_SUFFIX,
    material_max_characters=settings.exam_topic_summary_material_max_chars,
    provider_failed_message=PROVIDER_FAILED_MESSAGE,
    invalid_structure_message=SUMMARY_INVALID_MESSAGE,
)

SPECS = {
    OUTPUT_TYPE_EXAM_TOPIC_GUIDE: GUIDE_SPEC,
    OUTPUT_TYPE_EXAM_TOPIC_SUMMARY: SUMMARY_SPEC,
}


class ExamTopicStudyService:
    @staticmethod
    def generate(
        db: Session,
        course_id: int,
        topic: PlannedTopic,
        provider: TextGenerationProvider,
        *,
        user_id: int,
        output_type: str,
    ) -> ExamArtifactGeneration:
        return ExamArtifactService.generate(
            db,
            course_id,
            topic,
            provider,
            user_id=user_id,
            spec=SPECS[output_type],
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
    ) -> PersistedArtifact:
        spec = SPECS[output_type]
        supplied = generation.material.citation_map
        topic = generation.topic

        if output_type == OUTPUT_TYPE_EXAM_TOPIC_GUIDE:
            document = _guide_document(generation, supplied)
        else:
            document = _summary_document(generation, supplied)

        applied = ExamArtifactGenerationSettings(
            output_type=output_type,
            topic_key=topic.topic_key,
            display_label=topic.display_label,
            plan_output_id=topic.plan_output_id,
            analysis_output_id=topic.analysis_output_id,
            document_ids_requested=list(topic.document_ids),
            retrieval_limit=settings.retrieval_chunk_limit,
            retrieval_min_similarity=settings.retrieval_min_similarity,
            material_max_characters=spec.material_max_characters,
            topic_key_version=TOPIC_KEY_VERSION,
            prompt_template=spec.prompt_template,
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
            output_type=output_type,
            content=document.model_dump_json(),
            model_used=generation.model_used,
            generation_settings=applied.model_dump_json(),
            generation_context=context.model_dump_json(),
        )
        return PersistedArtifact(
            output=output,
            document=document,
            credits_charged=generation.unlock.amount,
        )

    @staticmethod
    def latest(
        db: Session, course_id: int, output_type: str, *, topic_key: str
    ) -> GeneratedOutput | None:
        return ExamArtifactService.latest(
            db, course_id, output_type, topic_key=topic_key
        )


def _cited(value, supplied: dict[str, SuppliedCitation]) -> dict:
    return {
        "text": value.text,
        "citations": [
            citation.model_dump(mode="json")
            for citation in resolve_citations(value.citations, supplied)
        ],
    }


def _guide_document(
    generation: ExamArtifactGeneration, supplied: dict[str, SuppliedCitation]
) -> ExamTopicGuideDocument:
    guide: GeneratedExamTopicGuide = generation.validated
    topic = generation.topic
    return ExamTopicGuideDocument(
        topic_key=topic.topic_key,
        display_label=topic.display_label,
        plan_output_id=topic.plan_output_id,
        rank=topic.rank,
        priority_band=topic.priority_band,
        title=guide.title,
        overview=_cited(guide.overview, supplied),
        sections=[
            {
                "heading": section.heading,
                "body": _cited(section.body, supplied),
                "key_points": [_cited(point, supplied) for point in section.key_points],
            }
            for section in guide.sections
        ],
        key_terms=[
            {
                "term": term.term,
                "definition": term.definition,
                "citations": [
                    Citation.model_validate(citation.model_dump(mode="json"))
                    for citation in resolve_citations(term.citations, supplied)
                ],
            }
            for term in guide.key_terms
        ],
        common_pitfalls=[
            {
                "mistake": pitfall.mistake,
                "correction": pitfall.correction,
                "citations": [
                    Citation.model_validate(citation.model_dump(mode="json"))
                    for citation in resolve_citations(pitfall.citations, supplied)
                ],
            }
            for pitfall in guide.common_pitfalls
        ],
        what_to_be_able_to_do=[
            _cited(item, supplied) for item in guide.what_to_be_able_to_do
        ],
        coverage=guide.coverage,
        confidence_notes=guide.confidence_notes,
    )


def _summary_document(
    generation: ExamArtifactGeneration, supplied: dict[str, SuppliedCitation]
) -> ExamTopicSummaryDocument:
    summary: GeneratedExamTopicSummary = generation.validated
    topic = generation.topic
    return ExamTopicSummaryDocument(
        topic_key=topic.topic_key,
        display_label=topic.display_label,
        plan_output_id=topic.plan_output_id,
        rank=topic.rank,
        priority_band=topic.priority_band,
        title=summary.title,
        summary=_cited(summary.summary, supplied),
        key_points=[_cited(point, supplied) for point in summary.key_points],
        coverage=summary.coverage,
        confidence_notes=summary.confidence_notes,
    )
