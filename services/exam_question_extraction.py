"""Reading the questions out of a past exam paper, once, when it is uploaded.

A question belongs to the paper it was printed in. Extracting it during an exam
analysis would tie it to whichever analysis happened to run, re-read an
unchanged paper on every rescan, and let two analyses of one paper disagree
about what it asks. Extraction therefore happens in the upload worker, against
the whole document rather than a retrieval-bounded slice, and every later
analysis reads the same rows.

It is best-effort and free. A paper whose questions could not be read is still
a usable source: its text is indexed, it still counts as material, and the
document records why extraction did not produce anything instead of failing the
upload. Nothing here may raise into the pipeline.

Question boundaries could be found with a parser. Separating a question from
the rubric printed beside it, associating a mark scheme with the right
question, and naming the topic a question assesses could not; a parser that
succeeded on tidy papers and silently mangled the rest would be worse than one
honest provider pass, because its failures would be invisible.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models import (
    EXAM_EXTRACTION_FAILED,
    EXAM_EXTRACTION_NOT_APPLICABLE,
    EXAM_EXTRACTION_NOT_CONFIGURED,
    EXAM_EXTRACTION_PENDING,
    EXAM_EXTRACTION_SKIPPED,
    EXAM_EXTRACTION_SUCCEEDED,
    PastExamQuestion,
    UploadedDocument,
)
from schemas.ai_usage import ErrorCategory, GenerationType
from schemas.exam_mode import GeneratedPastExamExtraction
from schemas.prompt_context import PromptContext
from services.ai_usage_logger import AiUsageLogger
from services.citations import SuppliedCitation, resolve_citations
from services.document_material import load_document_material
from services.exam_topics import canonical_topic_key
from services.prompt_context import resolve_prompt_context
from services.prompt_loader import PromptLoader
from services.text_generation import (
    TextGenerationError,
    TextGenerationProvider,
    get_text_generation_provider,
    with_template_temperature,
)

logger = logging.getLogger(__name__)

READY_STATUS = "ready"
PAST_EXAM_MATERIAL_KIND = "past_exam"


@dataclass(frozen=True)
class ExtractionOutcome:
    """What one extraction attempt produced, and why it produced that."""

    status: str
    question_count: int = 0
    error_code: str | None = None
    truncated: bool = False
    chunks_used: int = 0
    chunks_available: int = 0


class PastExamExtractionService:
    PROMPT_TEMPLATE_NAME = "past_exam_question_extraction"
    PROMPT_PATH = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "prompts"
        / "past_exam_question_extraction.json"
    )

    @classmethod
    def build_prompt(cls, paper_text: str, *, context: PromptContext) -> str:
        return PromptLoader.render(
            cls.PROMPT_TEMPLATE_NAME,
            {
                **context.as_variables(),
                "TEXT": paper_text,
            },
        )

    @staticmethod
    def is_extractable(document: UploadedDocument | None) -> bool:
        """Whether this document is a paper worth spending a provider call on.

        The material kind is student-declared, so this is a claim rather than a
        fact. It is still the only thing that distinguishes a paper from a
        lecture that discusses one, and the prompt is told to return nothing
        when the text turns out not to be an examination paper after all.
        """
        return (
            document is not None
            and document.status == READY_STATUS
            and document.material_kind == PAST_EXAM_MATERIAL_KIND
        )

    @staticmethod
    def needs_extraction(document: UploadedDocument) -> bool:
        """Whether a paper still owes an extraction attempt.

        ``succeeded`` and ``not_configured`` are settled: the first has its
        questions, and the second will keep failing until a deployment changes.
        Everything else is worth one more attempt the next time Exam Mode
        needs this paper.
        """
        return document.exam_extraction_status not in (
            EXAM_EXTRACTION_SUCCEEDED,
            EXAM_EXTRACTION_NOT_CONFIGURED,
        )

    @classmethod
    def extract(
        cls,
        db: Session,
        document: UploadedDocument,
        provider: TextGenerationProvider,
        *,
        prompt_context: PromptContext,
    ) -> ExtractionOutcome:
        """Read one paper and replace its recorded questions with what it says.

        Replaces rather than appends: a reprocessed document has new chunks,
        and questions read from text that no longer exists would outlive the
        page they cite. This is the one place ``past_exam_questions`` is
        written, and it commits only after both halves have been staged.
        """
        material = load_document_material(
            db,
            document.course_id,
            [document.id],
            max_characters=settings.exam_past_paper_max_chars,
        )
        if material.is_empty:
            return ExtractionOutcome(
                status=EXAM_EXTRACTION_SKIPPED,
                chunks_available=material.chunks_available,
            )

        prompt = cls.build_prompt(material.text, context=prompt_context)

        metadata = None
        try:
            # The template's own declared temperature, applied to the call it was declared for.
            provider = with_template_temperature(
                provider, PromptLoader.temperature_for(cls.PROMPT_TEMPLATE_NAME)
            )
            if hasattr(provider, "generate_json_with_metadata"):
                result, metadata = provider.generate_json_with_metadata(prompt)
            else:
                result = provider.generate_json(prompt)
        except TextGenerationError as exc:
            category = getattr(exc, "error_category", ErrorCategory.PROVIDER_ERROR)
            cls._log_failure(db, document, category)
            return ExtractionOutcome(
                status=EXAM_EXTRACTION_FAILED,
                error_code=_category_value(category),
                chunks_available=material.chunks_available,
            )

        try:
            validated = GeneratedPastExamExtraction.model_validate(result)
        except ValidationError:
            cls._log_failure(db, document, ErrorCategory.INVALID_STRUCTURE)
            return ExtractionOutcome(
                status=EXAM_EXTRACTION_FAILED,
                error_code=_category_value(ErrorCategory.INVALID_STRUCTURE),
                chunks_available=material.chunks_available,
            )

        rows = _question_rows(validated, supplied=material.citation_map)

        db.execute(
            delete(PastExamQuestion).where(
                PastExamQuestion.course_id == document.course_id,
                PastExamQuestion.document_id == document.id,
            )
        )
        db.add_all(
            PastExamQuestion(
                document_id=document.id,
                course_id=document.course_id,
                position=position,
                **row,
            )
            for position, row in enumerate(rows)
        )

        AiUsageLogger.log_success(
            db,
            user_id=document.user_id,
            course_id=document.course_id,
            generation_type=GenerationType.PAST_EXAM_EXTRACTION,
            metadata=metadata,
        )

        return ExtractionOutcome(
            status=EXAM_EXTRACTION_SUCCEEDED,
            question_count=len(rows),
            truncated=material.truncated,
            chunks_used=material.chunks_used,
            chunks_available=material.chunks_available,
        )

    @staticmethod
    def _log_failure(
        db: Session, document: UploadedDocument, category: ErrorCategory | str
    ) -> None:
        AiUsageLogger.log_failure(
            db,
            user_id=document.user_id,
            course_id=document.course_id,
            generation_type=GenerationType.PAST_EXAM_EXTRACTION,
            error_category=category,
        )

    @classmethod
    def run(
        cls,
        db: Session,
        document_id: UUID,
        *,
        provider_factory: Callable[[], TextGenerationProvider] | None = None,
    ) -> ExtractionOutcome:
        """Extract one paper and record the outcome on the document.

        Never raises. Every failure branch still leaves the document with a
        status a reader can act on, because the caller is a pipeline that must
        finish whatever happens here.
        """
        document = db.get(UploadedDocument, document_id)
        if not cls.is_extractable(document):
            return ExtractionOutcome(status=EXAM_EXTRACTION_NOT_APPLICABLE)

        _record_outcome(db, document, ExtractionOutcome(status=EXAM_EXTRACTION_PENDING))

        factory = provider_factory or get_text_generation_provider
        try:
            provider = factory()
        except Exception:
            logger.warning(
                "No text generation provider is available for past exam extraction"
            )
            outcome = ExtractionOutcome(status=EXAM_EXTRACTION_NOT_CONFIGURED)
            _record_outcome(db, document, outcome)
            return outcome

        try:
            prompt_context = resolve_prompt_context(
                db, course=document.course, document_ids=[document.id]
            )
            outcome = cls.extract(db, document, provider, prompt_context=prompt_context)
        except Exception:
            db.rollback()
            logger.exception("Past exam question extraction failed for a document")
            outcome = ExtractionOutcome(
                status=EXAM_EXTRACTION_FAILED,
                error_code=_category_value(ErrorCategory.UNKNOWN_ERROR),
            )
            document = db.get(UploadedDocument, document_id)
            if document is not None:
                _record_outcome(db, document, outcome)
            return outcome

        if outcome.truncated:
            # Said out loud rather than hidden. A paper read in order runs out
            # of budget at its end, so the questions this attempt may not have
            # seen are precisely the last ones.
            logger.warning(
                "A past exam paper was cut short at %s of %s passages",
                outcome.chunks_used,
                outcome.chunks_available,
            )
        _record_outcome(db, document, outcome)
        return outcome

    @staticmethod
    def load_questions(
        db: Session,
        course_id: int,
        document_ids: list[UUID],
        *,
        topic_key: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[list[PastExamQuestion], int]:
        """The recorded questions of the named papers, in paper order."""
        if not document_ids:
            return [], 0

        predicates = [
            PastExamQuestion.course_id == course_id,
            PastExamQuestion.document_id.in_(list(dict.fromkeys(document_ids))),
        ]
        if topic_key:
            predicates.append(PastExamQuestion.topic_key == topic_key)

        total = len(db.scalars(select(PastExamQuestion.id).where(*predicates)).all())
        statement = (
            select(PastExamQuestion)
            .where(*predicates)
            .order_by(PastExamQuestion.document_id, PastExamQuestion.position)
            .offset(offset)
        )
        if limit is not None:
            statement = statement.limit(limit)
        return list(db.scalars(statement).all()), total


def extract_past_exam_questions(
    session_factory: Callable[[], Session], document_id: UUID
) -> ExtractionOutcome:
    """Worker entry point. Opens its own session and never raises."""
    try:
        with session_factory() as session:
            outcome = PastExamExtractionService.run(session, document_id)
    except Exception:
        logger.exception("Past exam question extraction could not be attempted")
        return ExtractionOutcome(
            status=EXAM_EXTRACTION_FAILED,
            error_code=_category_value(ErrorCategory.UNKNOWN_ERROR),
        )
    return outcome


def _record_outcome(
    db: Session, document: UploadedDocument, outcome: ExtractionOutcome
) -> None:
    document.exam_extraction_status = outcome.status
    document.exam_extraction_error_code = outcome.error_code
    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Past exam extraction status could not be recorded")


def _category_value(category: ErrorCategory | str) -> str:
    return category.value if isinstance(category, ErrorCategory) else str(category)


def _question_rows(
    extraction: GeneratedPastExamExtraction, *, supplied: dict[str, SuppliedCitation]
) -> tuple[dict, ...]:
    """Turn validated provider questions into rows, resolving every source.

    Pages come from citations, never from the model: a model cannot know a
    PDF's page numbering, and a citation has already been checked against the
    passages actually supplied. A question whose citations resolve to nothing
    still belongs to this paper; it simply cannot be shown with a page.

    Topic keys are computed here rather than matched against anything, because
    extraction does not know what an analysis will later call a topic.
    ``canonical_topic_key`` is a pure function of a label, so the same key
    falls out at both ends without either side storing a link.
    """
    rows: list[dict] = []
    for question in extraction.questions:
        citations = resolve_citations(question.citations, supplied)
        origin = citations[0] if citations else None
        labels = [label.strip() for label in question.topics if label.strip()]
        mappings = [
            {"topic_key": key, "display_label": label}
            for key, label in ((canonical_topic_key(label), label) for label in labels)
            if key
        ]
        rows.append(
            {
                "page_start": origin.page_start if origin else None,
                "page_end": (
                    (origin.page_end or origin.page_start) if origin else None
                ),
                "question_label": question.question_label,
                "question_number": question.question_number,
                "question_text": question.question_text.strip(),
                "subparts": [
                    subpart.model_dump(mode="json") for subpart in question.subparts
                ]
                or None,
                "question_type": question.question_type.value,
                "difficulty": (
                    question.difficulty.value if question.difficulty else None
                ),
                "marks": question.marks,
                "answer_guidance": question.answer_guidance,
                "marking_points": list(question.marking_points) or None,
                "visual_refs": [
                    visual.model_dump(mode="json") for visual in question.visual_refs
                ]
                or None,
                "topic_key": mappings[0]["topic_key"] if mappings else None,
                "topic_mappings": mappings or None,
                "citations": [
                    citation.model_dump(mode="json") for citation in citations
                ]
                or None,
            }
        )
    return tuple(rows)
