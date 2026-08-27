"""The one ladder every per-topic Exam Mode artifact climbs.

A study guide for a topic, a summary of it, its practice questions, its topic
exam, and its similar questions all do the same seven things: resolve the plan,
check the topic is in it, unlock the topic, retrieve material narrowed to the
plan's own sources, generate, validate, and persist. Written five times that
would be five chances for one of them to charge without refunding, to reach a
document the plan never selected, or to accept a topic nobody planned.

So it is written once. Each artifact supplies a specification — its prompt, its
response model, its output type — and nothing else.

Two things are load-bearing here. Retrieval is narrowed to the documents the
plan's analysis was given, so a guide for "Graph Traversal" cannot quietly
answer from a course the student excluded from their exam scope. And the topic
must be one the plan actually ranked: a topic key that is merely well-formed
buys nothing, because the price of a topic is the price of the plan's topic.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models import Course, GeneratedOutput
from schemas.ai_usage import ErrorCategory, GenerationType
from schemas.prompt_context import PromptContext
from services.ai_usage_logger import AiUsageLogger
from services.document_lock import acquire_generation_locks
from services.exam_entitlements import ExamEntitlementService, TopicUnlock
from services.exam_plan import ExamPlanService
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
)
from utils.ai_errors import (
    EXAM_PLAN_REQUIRED_MESSAGE,
    EXAM_TOPIC_NOT_DISCOVERED_MESSAGE,
    ExamPlanRequiredError,
    ExamTopicNotDiscoveredError,
    InsufficientCreditsError,
    InvalidGeneratedStructureError,
)
from utils.exceptions import NotFoundException
from utils.json_documents import parse_json_object

logger = logging.getLogger(__name__)


class ExamArtifactError(RuntimeError):
    """A per-topic Exam Mode artifact could not be produced."""


class ExamArtifactPlanMissingError(ExamArtifactError, ExamPlanRequiredError):
    """This course has no exam plan, so no topic has been chosen to study."""


class ExamArtifactTopicNotPlannedError(ExamArtifactError, ExamTopicNotDiscoveredError):
    """The requested topic is not one the plan ranked."""


class InvalidExamArtifactStructureError(
    ExamArtifactError, InvalidGeneratedStructureError
):
    """The provider returned something this artifact cannot be built from."""


@dataclass(frozen=True)
class PlannedTopic:
    """One topic of one plan, with everything a generation needs about it."""

    plan_output_id: int
    analysis_output_id: int
    topic_key: str
    display_label: str
    rank: int
    priority_band: str
    is_high_priority: bool
    mastery_percentage: int | None
    document_ids: tuple[UUID, ...]

    @property
    def retrieval_scope(self) -> tuple[UUID, ...] | None:
        """The documents to search, or ``None`` meaning the whole course.

        An analysis run over every ready document records no explicit
        selection, and that is exactly the case where narrowing would be
        wrong: the student did not exclude anything.
        """
        return self.document_ids or None


@dataclass(frozen=True)
class ExamArtifactSpec:
    """Everything that differs between one per-topic artifact and another."""

    output_type: str
    generation_type: GenerationType
    prompt_template: str
    response_model: type[BaseModel]
    build_prompt: Callable[[str, PlannedTopic, PromptContext], str]
    retrieval_query_suffix: str
    material_max_characters: int
    provider_failed_message: str
    invalid_structure_message: str


@dataclass(frozen=True)
class ExamArtifactGeneration:
    """One completed artifact, not yet written to the database."""

    validated: BaseModel
    material: RetrievedCourseMaterial
    model_used: str
    topic: PlannedTopic
    unlock: TopicUnlock
    prompt_version: str


class ExamArtifactService:
    @staticmethod
    def resolve_topic(
        db: Session,
        course_id: int,
        topic_key: str,
        *,
        plan_output_id: int | None = None,
    ) -> PlannedTopic:
        """Find the topic in the plan, or refuse with a named next action.

        A missing plan and an unplanned topic are different conflicts, because
        the remedies differ: one is "make a plan", the other is "that is not
        one of your topics". Neither is a 404, because the course is fine.

        Nothing is charged before this returns, so probing a topic key costs
        nothing.
        """
        if plan_output_id is not None:
            plan = ExamPlanService.get_plan(db, course_id, plan_output_id)
        else:
            plan = ExamPlanService.latest_plan(db, course_id)
            if plan is None:
                raise ExamArtifactPlanMissingError(EXAM_PLAN_REQUIRED_MESSAGE)

        stored = (
            parse_json_object(
                plan.content,
                field="content",
                table="generated_outputs",
                row_id=plan.id,
            )
            or {}
        )
        entry = next(
            (
                topic
                for topic in stored.get("topics", [])
                if isinstance(topic, dict) and topic.get("topic_key") == topic_key
            ),
            None,
        )
        if entry is None:
            raise ExamArtifactTopicNotPlannedError(EXAM_TOPIC_NOT_DISCOVERED_MESSAGE)

        analysis_output_id = stored.get("analysis_output_id")
        return PlannedTopic(
            plan_output_id=plan.id,
            analysis_output_id=(
                analysis_output_id if isinstance(analysis_output_id, int) else 0
            ),
            topic_key=topic_key,
            display_label=str(entry.get("display_label") or topic_key),
            rank=int(entry.get("rank") or 0),
            priority_band=str(entry.get("priority_band") or ""),
            is_high_priority=bool(entry.get("is_high_priority")),
            mastery_percentage=(
                entry.get("mastery_percentage")
                if isinstance(entry.get("mastery_percentage"), int)
                else None
            ),
            document_ids=_analysis_scope(db, analysis_output_id),
        )

    @staticmethod
    def get_course_material(
        db: Session,
        course_id: int,
        *,
        query: str,
        document_ids: Sequence[UUID] | None,
        max_characters: int,
    ) -> RetrievedCourseMaterial:
        return load_retrieved_material(
            db,
            course_id,
            query=query,
            limit=settings.retrieval_chunk_limit,
            min_similarity=settings.retrieval_min_similarity,
            max_characters=max_characters,
            include_citations=True,
            document_ids=document_ids,
        )

    @classmethod
    def generate(
        cls,
        db: Session,
        course_id: int,
        topic: PlannedTopic,
        provider: TextGenerationProvider,
        *,
        user_id: int,
        spec: ExamArtifactSpec,
    ) -> ExamArtifactGeneration:
        """Unlock the topic, read its material, and produce one artifact.

        Persists nothing but the unlock, which has to be committed before the
        work it pays for starts. Every failure after that point releases it,
        so a student is never left holding a topic whose first artifact never
        arrived — the same promise every other generation in this codebase
        makes by refunding.
        """
        course = db.get(Course, course_id)

        def log_failure(category: ErrorCategory, **extra) -> None:
            AiUsageLogger.log_failure(
                db,
                user_id=user_id,
                course_id=course_id,
                generation_type=spec.generation_type,
                error_category=category,
                **extra,
            )
            try:
                db.commit()
            except Exception:
                db.rollback()

        try:
            unlock = ExamEntitlementService.ensure_unlocked(
                db, course_id, user_id, topic.topic_key
            )
        except Exception as exc:
            log_failure(_category_for(exc))
            raise

        query = build_retrieval_query(
            course, topic.display_label, suffix=spec.retrieval_query_suffix
        )

        try:
            material = cls.get_course_material(
                db,
                course_id,
                query=query,
                document_ids=topic.retrieval_scope,
                max_characters=spec.material_max_characters,
            )
        except MaterialNotIndexedError:
            ExamEntitlementService.release(db, unlock)
            log_failure(ErrorCategory.MATERIAL_NOT_INDEXED)
            raise
        except NoRelevantMaterialError:
            ExamEntitlementService.release(db, unlock)
            log_failure(ErrorCategory.NO_RELEVANT_MATERIAL)
            raise
        except MaterialRetrievalError:
            ExamEntitlementService.release(db, unlock)
            log_failure(ErrorCategory.RETRIEVAL_ERROR)
            raise
        except Exception:
            ExamEntitlementService.release(db, unlock)
            raise

        with acquire_generation_locks(material.document_ids):
            prompt_context = resolve_prompt_context(db, course=course, user_id=user_id)
            prompt = spec.build_prompt(material.text, topic, prompt_context)

            metadata = None
            try:
                if hasattr(provider, "generate_json_with_metadata"):
                    result, metadata = provider.generate_json_with_metadata(prompt)
                else:
                    result = provider.generate_json(prompt)
            except TextGenerationError as exc:
                ExamEntitlementService.release(db, unlock)
                log_failure(
                    getattr(exc, "error_category", ErrorCategory.PROVIDER_ERROR)
                )
                raise ExamArtifactError(spec.provider_failed_message) from exc
            except Exception:
                ExamEntitlementService.release(db, unlock)
                raise

            try:
                validated = spec.response_model.model_validate(result)
            except ValidationError as exc:
                ExamEntitlementService.release(db, unlock)
                log_failure(
                    ErrorCategory.INVALID_STRUCTURE,
                    latency_ms=metadata.latency_ms if metadata else None,
                )
                raise InvalidExamArtifactStructureError(
                    spec.invalid_structure_message
                ) from exc

            AiUsageLogger.log_success(
                db,
                user_id=user_id,
                course_id=course_id,
                generation_type=spec.generation_type,
                metadata=metadata,
            )

            return ExamArtifactGeneration(
                validated=validated,
                material=material,
                model_used=model_identifier(metadata),
                topic=topic,
                unlock=unlock,
                prompt_version=PromptLoader.load_template(spec.prompt_template).version,
            )

    @staticmethod
    def persist(
        db: Session,
        course_id: int,
        *,
        user_id: int,
        output_type: str,
        content: str,
        model_used: str | None,
        generation_settings: str,
        generation_context: str,
    ) -> GeneratedOutput:
        return GeneratedOutputService.record(
            db,
            course_id=course_id,
            user_id=user_id,
            output_type=output_type,
            content=content,
            model_used=model_used,
            generation_settings=generation_settings,
            generation_context=generation_context,
        )

    @staticmethod
    def latest(
        db: Session, course_id: int, output_type: str, *, topic_key: str
    ) -> GeneratedOutput | None:
        """The newest artifact of this kind for this topic, or nothing.

        Matched through the stored settings document rather than a column,
        because the topic key belongs to the artifact's own contract and
        ``generated_outputs`` stays a table of generations rather than a table
        of Exam Mode.
        """
        rows = db.scalars(
            select(GeneratedOutput)
            .where(
                GeneratedOutput.course_id == course_id,
                GeneratedOutput.output_type == output_type,
            )
            .order_by(GeneratedOutput.created_at.desc(), GeneratedOutput.id.desc())
        ).all()
        for row in rows:
            stored = (
                parse_json_object(
                    row.generation_settings,
                    field="generation_settings",
                    table="generated_outputs",
                    row_id=row.id,
                )
                or {}
            )
            if stored.get("topic_key") == topic_key:
                return row
        return None

    @staticmethod
    def get(
        db: Session, course_id: int, output_type: str, output_id: int
    ) -> GeneratedOutput:
        output = db.scalar(
            select(GeneratedOutput).where(
                GeneratedOutput.id == output_id,
                GeneratedOutput.course_id == course_id,
                GeneratedOutput.output_type == output_type,
            )
        )
        if output is None:
            raise NotFoundException(detail="Exam Mode output not found")
        return output


def _analysis_scope(db: Session, analysis_output_id: object) -> tuple[UUID, ...]:
    """The documents the plan's analysis was asked to read, if it was narrowed.

    Read from the analysis's own settings rather than from what retrieval
    happened to return, because the student's selection is the scope of their
    exam and a relevance miss is not a change of mind.
    """
    if not isinstance(analysis_output_id, int):
        return ()
    analysis = db.get(GeneratedOutput, analysis_output_id)
    if analysis is None:
        return ()
    stored = (
        parse_json_object(
            analysis.generation_settings,
            field="generation_settings",
            table="generated_outputs",
            row_id=analysis.id,
        )
        or {}
    )
    requested = stored.get("document_ids_requested")
    if not isinstance(requested, list):
        return ()
    resolved: list[UUID] = []
    for value in requested:
        try:
            resolved.append(UUID(str(value)))
        except (TypeError, ValueError):
            continue
    return tuple(dict.fromkeys(resolved))


def _category_for(exc: Exception) -> ErrorCategory:
    if isinstance(exc, InsufficientCreditsError):
        return ErrorCategory.INSUFFICIENT_CREDITS
    return ErrorCategory.UNKNOWN_ERROR
