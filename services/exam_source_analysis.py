"""Reading a course's chosen exam sources into durable, queryable evidence.

An analysis discovers topics. It does not read past papers: their questions
were transcribed once, when each paper was uploaded, and this pass counts the
rows that already exist. That split is what makes a rescan cheap and what stops
two analyses of one paper disagreeing about what it asks.

The two halves still meet on one vocabulary. ``canonical_topic_key`` is a pure
function of a label, so a question keyed by its extractor months ago resolves
against today's candidates with nothing stored to connect them, exactly as a
mastery label does.

Nothing here reprocesses a document. The course's material is already
extracted, chunked, and embedded; "analyse these sources" means read what is
already indexed, narrowed to the documents the student chose, and never widen
that to material they did not ask about. A selected paper that never got its
questions read is healed here, once, and the result belongs to the paper.

The result is immutable. A later scan writes a new analysis rather than editing
this one, so a plan built today can still be reopened against the evidence it
was actually built from.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models import (
    OUTPUT_TYPE_EXAM_TOPIC_ANALYSIS,
    Course,
    CourseTopic,
    ExamTopicCandidate,
    GeneratedOutput,
    PastExamQuestion,
    UploadedDocument,
)
from schemas.ai_usage import ErrorCategory, GenerationType
from schemas.exam_mode import (
    ExamAnalysisGenerationContext,
    ExamAnalysisGenerationSettings,
    ExamAnalysisRequest,
    ExamAnalysisSummaryDocument,
    ExamSourceDocument,
    ExamSourceInventory,
    GeneratedExamAnalysisResponse,
)
from schemas.prompt_context import PromptContext
from services.ai_usage_logger import AiUsageLogger
from services.citations import SuppliedCitation, document_label, resolve_citations
from services.course_material import count_available_chunks
from services.credits import GENERATION_CREDIT_COSTS, ChargeReceipt, CreditService
from services.document_lock import acquire_generation_locks
from services.exam_question_extraction import PastExamExtractionService
from services.exam_topics import (
    TOPIC_KEY_VERSION,
    KeyedCandidate,
    RawCandidate,
    TopicEvidence,
    build_topic_index,
    canonical_topic_key,
    key_candidates,
    match_topic_key,
)
from services.generated_output import GeneratedOutputService
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
    with_template_temperature,
)
from utils.ai_errors import (
    NO_READY_MATERIAL_MESSAGE,
    SOURCE_NOT_READY_MESSAGE,
    CourseMaterialUnavailableError,
    InsufficientCreditsError,
    InvalidGeneratedStructureError,
    SourceDocumentNotReadyError,
)
from utils.exceptions import NotFoundException
from utils.json_documents import parse_json_object

READY_STATUS = "ready"
PAST_EXAM_MATERIAL_KIND = "past_exam"
SYLLABUS_MATERIAL_KIND = "syllabus"

DOCUMENT_NOT_FOUND = "Document not found"

RETRIEVAL_QUERY_SUFFIX = "exam scope assessed topics examination questions"

NO_SYLLABUS_TEXT = "No syllabus text was supplied for this course."
NO_DECLARED_TOPICS_TEXT = "The student has not declared any course topics."

MAX_SYLLABUS_PROMPT_CHARS = 20_000
MAX_DECLARED_TOPICS_PROMPT_CHARS = 4_000


class ExamModeError(RuntimeError):
    """Exam Mode work could not be completed."""


class NoExamSourceMaterialError(ExamModeError, CourseMaterialUnavailableError):
    """This course has no processed material to analyse at all."""


class ExamSourceNotReadyError(ExamModeError, SourceDocumentNotReadyError):
    """A selected source document has not finished processing."""


class InvalidExamAnalysisStructureError(ExamModeError, InvalidGeneratedStructureError):
    """The provider returned something that is not a usable topic analysis."""


@dataclass(frozen=True)
class ExamAnalysisGeneration:
    """One completed analysis, not yet written to the database."""

    candidates: tuple[KeyedCandidate, ...]
    question_count: int
    material: RetrievedCourseMaterial
    model_used: str
    coverage: object
    confidence_notes: str
    documents_analysed: tuple[UUID, ...]
    past_exam_documents: tuple[UUID, ...]
    syllabus_present: bool
    course_topics_promoted: int
    effective_request: ExamAnalysisRequest
    prompt_version: str
    charge_receipt: ChargeReceipt | None = None


class ExamSourceAnalysisService:
    PROMPT_TEMPLATE_NAME = "exam_topic_analysis"
    PROMPT_PATH = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "prompts"
        / "exam_topic_analysis.json"
    )

    @staticmethod
    def list_sources(db: Session, course_id: int) -> ExamSourceInventory:
        """What this course could supply to an analysis. Read-only and free."""
        course = db.get(Course, course_id)
        syllabus = (course.syllabus or "") if course is not None else ""

        rows = db.scalars(
            select(UploadedDocument)
            .where(UploadedDocument.course_id == course_id)
            .order_by(UploadedDocument.created_at, UploadedDocument.id)
        ).all()

        documents = [
            ExamSourceDocument(
                id=row.id,
                label=document_label(row.original_file_name),
                material_kind=row.material_kind,
                status=row.status,
                is_past_exam=row.material_kind == PAST_EXAM_MATERIAL_KIND,
                is_syllabus=row.material_kind == SYLLABUS_MATERIAL_KIND,
            )
            for row in rows
            if row.status != "deleting"
        ]

        topics = db.scalars(
            select(CourseTopic.name)
            .where(CourseTopic.course_id == course_id)
            .order_by(CourseTopic.position)
        ).all()

        return ExamSourceInventory(
            syllabus_present=bool(syllabus.strip()),
            syllabus_characters=len(syllabus),
            course_topics=list(topics),
            documents=documents,
            ready_document_count=sum(
                1 for row in documents if row.status == READY_STATUS
            ),
            past_exam_document_count=sum(
                1
                for row in documents
                if row.is_past_exam and row.status == READY_STATUS
            ),
            chunks_available=count_available_chunks(db, course_id),
        )

    @staticmethod
    def resolve_selected_documents(
        db: Session, course_id: int, document_ids: Sequence[UUID] | None
    ) -> list[UUID]:
        """Validate the student's source selection before anything is spent.

        A document belonging to another course is answered exactly as a missing
        one, so an identifier cannot be probed. A document that exists but is
        still processing gets its own conflict, because the retrieval layer
        would otherwise report it as an indexing gap or a relevance miss and
        send the student after a problem that fixes itself.

        Runs before the credit charge, so naming a processing document never
        costs anything.
        """
        if document_ids is None:
            return []

        requested = list(dict.fromkeys(document_ids))
        rows = db.execute(
            select(UploadedDocument.id, UploadedDocument.status).where(
                UploadedDocument.course_id == course_id,
                UploadedDocument.id.in_(requested),
            )
        ).all()
        found = {row.id: row.status for row in rows}

        if any(identifier not in found for identifier in requested):
            raise NotFoundException(detail=DOCUMENT_NOT_FOUND)
        if any(found[identifier] != READY_STATUS for identifier in requested):
            raise ExamSourceNotReadyError(SOURCE_NOT_READY_MESSAGE)
        return requested

    @staticmethod
    def past_exam_document_ids(
        db: Session, course_id: int, document_ids: Sequence[UUID] | None
    ) -> list[UUID]:
        """The ready documents in scope that are actually examination papers.

        A lecture that discusses an exam is not a past paper. Only the
        ``past_exam`` material kind qualifies, which is what stops exam-like
        prose becoming past-exam frequency evidence.
        """
        predicates = [
            UploadedDocument.course_id == course_id,
            UploadedDocument.status == READY_STATUS,
            UploadedDocument.material_kind == PAST_EXAM_MATERIAL_KIND,
        ]
        if document_ids:
            predicates.append(UploadedDocument.id.in_(list(document_ids)))
        return list(
            db.scalars(
                select(UploadedDocument.id)
                .where(*predicates)
                .order_by(UploadedDocument.created_at, UploadedDocument.id)
            ).all()
        )

    @staticmethod
    def get_course_material(
        db: Session,
        course_id: int,
        *,
        query: str,
        document_ids: Sequence[UUID] | None,
    ) -> RetrievedCourseMaterial:
        return load_retrieved_material(
            db,
            course_id,
            query=query,
            limit=settings.retrieval_chunk_limit,
            min_similarity=settings.retrieval_min_similarity,
            max_characters=settings.exam_analysis_material_max_chars,
            include_citations=True,
            document_ids=document_ids or None,
        )

    @classmethod
    def build_prompt(
        cls,
        course_material: str,
        request: ExamAnalysisRequest,
        *,
        declared_topics: Sequence[str],
        syllabus: str | None,
        context: PromptContext,
    ) -> str:
        topics_text = ", ".join(name for name in declared_topics if name.strip())
        syllabus_text = (syllabus or "").strip()
        return PromptLoader.render(
            cls.PROMPT_TEMPLATE_NAME,
            {
                **context.as_variables(),
                "TOPIC_FOCUS": request.topic_focus,
                "DECLARED_TOPICS": (
                    topics_text[:MAX_DECLARED_TOPICS_PROMPT_CHARS]
                    or NO_DECLARED_TOPICS_TEXT
                ),
                "SYLLABUS_TEXT": (
                    syllabus_text[:MAX_SYLLABUS_PROMPT_CHARS] or NO_SYLLABUS_TEXT
                ),
                "TEXT": course_material,
            },
        )

    @classmethod
    def _promote_declared_topics(
        cls,
        candidates: tuple[KeyedCandidate, ...],
        declared: Sequence[str],
    ) -> tuple[tuple[KeyedCandidate, ...], int]:
        """Reconcile the student's declared topic list with what was discovered.

        A declared topic the analysis already found is marked as declared. One
        it did not find becomes a candidate of its own with no observed
        evidence, because a topic the student wrote down that no source
        mentions is exactly the gap an exam plan exists to surface.
        """
        index = build_topic_index(candidates)
        declared_keys: set[str] = set()
        unmatched: list[str] = []

        for name in declared:
            label = name.strip()
            if not label:
                continue
            matched = match_topic_key(label, index)
            if matched is not None:
                declared_keys.add(matched)
            else:
                unmatched.append(label)

        marked = tuple(
            candidate
            if candidate.topic_key not in declared_keys
            else replace(
                candidate,
                evidence=replace(candidate.evidence, in_course_topics=True),
            )
            for candidate in candidates
        )

        promoted: list[KeyedCandidate] = []
        seen = {candidate.topic_key for candidate in marked}
        for label in unmatched:
            key = canonical_topic_key(label)
            if not key or key in seen:
                continue
            seen.add(key)
            promoted.append(
                KeyedCandidate(
                    topic_key=key,
                    display_label=label[:200],
                    aliases=(),
                    evidence=TopicEvidence(
                        in_course_topics=True, discovery_confidence=1.0
                    ),
                    citation_keys=(),
                    alias_keys=(),
                )
            )

        return marked + tuple(promoted), len(promoted)

    @classmethod
    def ensure_questions_extracted(
        cls, db: Session, document_ids: Sequence[UUID]
    ) -> None:
        """Give any selected paper that never got read one attempt, now.

        Papers uploaded before extraction existed, and papers whose extraction
        failed, would otherwise contribute nothing to the past-exam signal
        forever. The attempt is free, best-effort, and belongs to the paper, so
        it is made once and every later analysis reads the result.
        """
        for document_id in document_ids:
            document = db.get(UploadedDocument, document_id)
            if document is None or not cls.is_extractable(document):
                continue
            if PastExamExtractionService.needs_extraction(document):
                PastExamExtractionService.run(db, document_id)

    @staticmethod
    def is_extractable(document: UploadedDocument) -> bool:
        return PastExamExtractionService.is_extractable(document)

    @staticmethod
    def past_exam_evidence(
        db: Session, course_id: int, document_ids: Sequence[UUID]
    ) -> dict[str, tuple[int, float | None]]:
        """How often each extracted topic label appears across these papers.

        Keyed by the question's own ``topic_key``, which was computed from the
        label its extractor gave it. Nothing is matched against a candidate
        here; that happens once the analysis knows what its candidates are.
        """
        questions, _ = PastExamExtractionService.load_questions(
            db, course_id, list(document_ids)
        )
        counts: dict[str, int] = {}
        marks: dict[str, float] = {}
        for question in questions:
            for mapping in question.topic_mappings or []:
                key = mapping.get("topic_key") if isinstance(mapping, dict) else None
                if not key:
                    continue
                counts[key] = counts.get(key, 0) + 1
                if question.marks is not None:
                    marks[key] = marks.get(key, 0.0) + float(question.marks)
        return {key: (count, marks.get(key)) for key, count in counts.items()}

    @classmethod
    def analyse(
        cls,
        db: Session,
        course_id: int,
        request: ExamAnalysisRequest,
        provider: TextGenerationProvider,
        *,
        user_id: int,
        rescan: bool = False,
    ) -> ExamAnalysisGeneration:
        """Read the selected sources once and return everything they yielded.

        Persists nothing. The caller writes the analysis and its evidence in
        one transaction so a crash can never leave an analysis row claiming an
        analysis that discovered nothing.
        """
        course = db.get(Course, course_id)

        def log_failure(category: ErrorCategory, **extra) -> None:
            AiUsageLogger.log_failure(
                db,
                user_id=user_id,
                course_id=course_id,
                generation_type=GenerationType.EXAM_TOPIC_ANALYSIS,
                error_category=category,
                **extra,
            )
            try:
                db.commit()
            except Exception:
                db.rollback()

        selected = cls.resolve_selected_documents(db, course_id, request.document_ids)

        if count_available_chunks(db, course_id) == 0:
            log_failure(ErrorCategory.NO_READY_MATERIAL)
            raise NoExamSourceMaterialError(NO_READY_MATERIAL_MESSAGE)

        past_exams = list(cls.past_exam_document_ids(db, course_id, selected or None))
        cls.ensure_questions_extracted(db, past_exams)
        declared = list(
            db.scalars(
                select(CourseTopic.name)
                .where(CourseTopic.course_id == course_id)
                .order_by(CourseTopic.position)
            ).all()
        )

        query = build_retrieval_query(
            course, request.topic_focus, suffix=RETRIEVAL_QUERY_SUFFIX
        )

        price = GENERATION_CREDIT_COSTS[
            "exam_topic_analysis_rescan" if rescan else "exam_topic_analysis"
        ]
        receipt = CreditService.charge(
            db,
            user_id,
            price,
            source_type=(
                "exam_topic_analysis_rescan" if rescan else "exam_topic_analysis"
            ),
        )
        if receipt is None:
            log_failure(ErrorCategory.INSUFFICIENT_CREDITS)
            raise InsufficientCreditsError("Insufficient credits.")

        try:
            material = cls.get_course_material(
                db, course_id, query=query, document_ids=selected or None
            )
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
        except Exception:
            db.rollback()
            CreditService.refund(db, receipt)
            raise

        with (
            CreditService.refund_on_error(db, receipt),
            acquire_generation_locks(material.document_ids),
        ):
            prompt_context = resolve_prompt_context(db, course=course, user_id=user_id)
            prompt = cls.build_prompt(
                material.text,
                request,
                declared_topics=declared,
                syllabus=course.syllabus if course is not None else None,
                context=prompt_context,
            )

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
                CreditService.refund(db, receipt)
                log_failure(
                    getattr(exc, "error_category", ErrorCategory.PROVIDER_ERROR)
                )
                raise ExamModeError("Text generation provider failed.") from exc
            except Exception:
                CreditService.refund(db, receipt)
                raise

            try:
                validated = GeneratedExamAnalysisResponse.model_validate(result)
            except ValidationError as exc:
                CreditService.refund(db, receipt)
                log_failure(
                    ErrorCategory.INVALID_STRUCTURE,
                    latency_ms=metadata.latency_ms if metadata else None,
                )
                raise InvalidExamAnalysisStructureError(
                    "Generated exam analysis has an invalid structure."
                ) from exc

            candidates = key_candidates(
                [
                    RawCandidate(
                        label=topic.label,
                        aliases=tuple(topic.aliases),
                        evidence=TopicEvidence(
                            in_syllabus=topic.in_syllabus,
                            in_course_topics=topic.in_course_topics,
                            in_material=topic.in_material,
                            discovery_confidence=topic.discovery_confidence,
                            syllabus_weight_percent=topic.syllabus_weight_percent,
                            syllabus_mention_count=topic.syllabus_mention_count,
                            material_chunk_count=topic.material_chunk_count,
                            material_character_count=topic.material_character_count,
                        ),
                        citation_keys=tuple(topic.citations),
                    )
                    for topic in validated.topics
                ]
            )
            candidates, promoted = cls._promote_declared_topics(candidates, declared)

            # Every selected paper counts, whether or not its own prose won a
            # place in the retrieved material. Its questions were read whole
            # when it was uploaded, and a retrieval budget is not allowed to
            # decide what an exam has asked.
            evidence = cls.past_exam_evidence(db, course_id, past_exams)
            candidates = _apply_question_counts(
                candidates, evidence, build_topic_index(candidates)
            )

            AiUsageLogger.log_success(
                db,
                user_id=user_id,
                course_id=course_id,
                generation_type=GenerationType.EXAM_TOPIC_ANALYSIS,
                metadata=metadata,
            )

            return ExamAnalysisGeneration(
                candidates=candidates,
                question_count=sum(
                    candidate.evidence.past_exam_question_count
                    for candidate in candidates
                ),
                material=material,
                model_used=model_identifier(metadata),
                coverage=validated.coverage,
                confidence_notes=validated.confidence_notes,
                documents_analysed=tuple(material.document_ids),
                past_exam_documents=tuple(past_exams),
                syllabus_present=bool(
                    course is not None and (course.syllabus or "").strip()
                ),
                course_topics_promoted=promoted,
                effective_request=request,
                prompt_version=PromptLoader.load_template(
                    cls.PROMPT_TEMPLATE_NAME
                ).version,
                charge_receipt=receipt,
            )

    @classmethod
    def build_documents(
        cls, generation: ExamAnalysisGeneration, *, rescan: bool
    ) -> tuple[str, str, str]:
        """The three JSON documents one analysis row carries."""
        summary = ExamAnalysisSummaryDocument(
            candidate_count=len(generation.candidates),
            past_exam_question_count=generation.question_count,
            documents_analysed=list(generation.documents_analysed),
            past_exam_documents_analysed=list(generation.past_exam_documents),
            syllabus_present=generation.syllabus_present,
            course_topics_promoted=generation.course_topics_promoted,
            coverage=generation.coverage,
            confidence_notes=generation.confidence_notes,
        )
        applied = ExamAnalysisGenerationSettings(
            topic_focus=generation.effective_request.topic_focus,
            rescan=rescan,
            document_ids_requested=list(generation.material.document_ids_requested),
            retrieval_limit=settings.retrieval_chunk_limit,
            retrieval_min_similarity=settings.retrieval_min_similarity,
            material_max_characters=settings.exam_analysis_material_max_chars,
            topic_key_version=TOPIC_KEY_VERSION,
            prompt_template=cls.PROMPT_TEMPLATE_NAME,
            prompt_version=generation.prompt_version,
        )
        context = ExamAnalysisGenerationContext.from_material(generation.material)
        context = context.model_copy(
            update={
                "documents_analysed": list(generation.documents_analysed),
                "past_exam_documents_analysed": list(generation.past_exam_documents),
                "candidates_discovered": len(generation.candidates),
                "questions_extracted": generation.question_count,
                "course_topics_promoted": generation.course_topics_promoted,
            }
        )
        return (
            summary.model_dump_json(),
            applied.model_dump_json(),
            context.model_dump_json(),
        )

    @classmethod
    def persist(
        cls,
        db: Session,
        course_id: int,
        generation: ExamAnalysisGeneration,
        *,
        user_id: int,
        rescan: bool = False,
    ) -> GeneratedOutput:
        """Write the analysis row and everything it discovered, atomically.

        Nothing here may partially succeed. An analysis row with no candidates
        would claim an analysis happened while ranking nothing, and because
        this table is append-only there would be no later write to repair it.
        ``GeneratedOutputService.record`` therefore stages the parent row
        without committing and this function owns the single commit, the same
        arrangement quiz generation already uses in the opposite direction.
        """
        content, applied_settings, applied_context = cls.build_documents(
            generation, rescan=rescan
        )
        try:
            output = GeneratedOutputService.record(
                db,
                course_id=course_id,
                user_id=user_id,
                output_type=OUTPUT_TYPE_EXAM_TOPIC_ANALYSIS,
                content=content,
                model_used=generation.model_used,
                generation_settings=applied_settings,
                generation_context=applied_context,
                commit=False,
            )
            db.add_all(
                ExamTopicCandidate(
                    analysis_output_id=output.id,
                    course_id=course_id,
                    position=position,
                    topic_key=candidate.topic_key,
                    display_label=candidate.display_label[:200],
                    aliases=list(candidate.aliases) or None,
                    in_syllabus=candidate.evidence.in_syllabus,
                    in_course_topics=candidate.evidence.in_course_topics,
                    in_past_exams=candidate.evidence.in_past_exams,
                    in_material=candidate.evidence.in_material,
                    discovery_confidence=candidate.evidence.discovery_confidence,
                    syllabus_weight_percent=candidate.evidence.syllabus_weight_percent,
                    syllabus_mention_count=candidate.evidence.syllabus_mention_count,
                    material_chunk_count=candidate.evidence.material_chunk_count,
                    material_character_count=(
                        candidate.evidence.material_character_count
                    ),
                    past_exam_question_count=(
                        candidate.evidence.past_exam_question_count
                    ),
                    past_exam_marks_total=candidate.evidence.past_exam_marks_total,
                    citations=_candidate_citations(
                        candidate, generation.material.citation_map
                    ),
                )
                for position, candidate in enumerate(generation.candidates)
            )
            db.flush()
            db.commit()
        except Exception:
            db.rollback()
            raise
        db.refresh(output)
        return output

    @staticmethod
    def latest_analysis(db: Session, course_id: int) -> GeneratedOutput | None:
        return db.scalar(
            select(GeneratedOutput)
            .where(
                GeneratedOutput.course_id == course_id,
                GeneratedOutput.output_type == OUTPUT_TYPE_EXAM_TOPIC_ANALYSIS,
            )
            .order_by(GeneratedOutput.created_at.desc(), GeneratedOutput.id.desc())
            .limit(1)
        )

    @staticmethod
    def get_analysis(db: Session, course_id: int, output_id: int) -> GeneratedOutput:
        """Load one analysis scoped to its course, or deny without disclosure."""
        output = db.scalar(
            select(GeneratedOutput).where(
                GeneratedOutput.id == output_id,
                GeneratedOutput.course_id == course_id,
                GeneratedOutput.output_type == OUTPUT_TYPE_EXAM_TOPIC_ANALYSIS,
            )
        )
        if output is None:
            raise NotFoundException(detail="Exam topic analysis not found")
        return output

    @staticmethod
    def load_candidates(
        db: Session, course_id: int, analysis_output_id: int
    ) -> Sequence[ExamTopicCandidate]:
        return db.scalars(
            select(ExamTopicCandidate)
            .where(
                ExamTopicCandidate.course_id == course_id,
                ExamTopicCandidate.analysis_output_id == analysis_output_id,
            )
            .order_by(ExamTopicCandidate.position)
        ).all()

    @staticmethod
    def load_questions(
        db: Session,
        course_id: int,
        analysis_output_id: int,
        *,
        topic_key: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Sequence[PastExamQuestion], int, list[UUID]]:
        """The questions of the papers one analysis had in scope.

        Scoped by paper rather than by analysis, because that is who owns
        them. Which papers were in scope is recorded in the analysis's own
        summary document, so reopening an older analysis still shows the
        questions it actually ranked from even after newer papers arrived.
        """
        output = db.get(GeneratedOutput, analysis_output_id)
        summary = (
            parse_json_object(
                output.content,
                field="content",
                table="generated_outputs",
                row_id=output.id,
            )
            or {}
            if output is not None
            else {}
        )
        raw = summary.get("past_exam_documents_analysed") or []
        document_ids = [
            identifier
            for identifier in (_as_uuid(value) for value in raw)
            if identifier is not None
        ]
        rows, total = PastExamExtractionService.load_questions(
            db,
            course_id,
            document_ids,
            topic_key=topic_key,
            limit=limit,
            offset=offset,
        )
        return rows, total, document_ids


def _as_uuid(value: object) -> UUID | None:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _candidate_citations(
    candidate: KeyedCandidate, supplied: dict[str, SuppliedCitation]
) -> list[dict] | None:
    citations = resolve_citations(candidate.citation_keys, supplied)
    return [citation.model_dump(mode="json") for citation in citations] or None


def _apply_question_counts(
    candidates: tuple[KeyedCandidate, ...],
    evidence: dict[str, tuple[int, float | None]],
    topic_index: dict[str, str],
) -> tuple[KeyedCandidate, ...]:
    """Fold the papers' recorded questions into per-topic past-exam evidence.

    An extractor keys a question by whatever the paper made it call the topic,
    which is rarely the wording this analysis chose. Both sides went through
    ``canonical_topic_key``, so the two vocabularies meet: a key that is not
    already a candidate is matched through the analysis's own topic index,
    which also resolves the aliases the model reported.

    Counted from questions actually recorded against a paper rather than from
    anything a model asserted, so the frequency signal can never exceed the
    evidence a reader could go and check.
    """
    counts: dict[str, int] = {}
    marks: dict[str, float] = {}
    known = {candidate.topic_key for candidate in candidates}

    for key, (count, total_marks) in evidence.items():
        resolved = key if key in known else match_topic_key(key, topic_index)
        if resolved is None:
            continue
        counts[resolved] = counts.get(resolved, 0) + count
        if total_marks is not None:
            marks[resolved] = marks.get(resolved, 0.0) + total_marks

    return tuple(
        replace(
            candidate,
            evidence=replace(
                candidate.evidence,
                in_past_exams=counts.get(candidate.topic_key, 0) > 0,
                past_exam_question_count=counts.get(candidate.topic_key, 0),
                past_exam_marks_total=marks.get(candidate.topic_key),
            ),
        )
        for candidate in candidates
    )


def analysis_created_at(output: GeneratedOutput) -> datetime:
    created = output.created_at
    if created.tzinfo is None:
        return created.replace(tzinfo=UTC)
    return created
