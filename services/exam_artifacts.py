"""The one ladder every generated Exam Mode artifact climbs.

A study guide for a topic, a summary of it, its practice questions, its topic
exam, its similar questions, the course's mock exam and its review sheet all do
the same things: resolve the plan, pay for the work, retrieve material narrowed
to the plan's own sources, generate, validate, and persist. Written seven times
that would be seven chances for one of them to charge without refunding, to
reach a document the plan never selected, or to accept a topic nobody planned.

So it is written once. Each artifact supplies a specification — its prompt, its
response model, its output type — and nothing else.

The two entry points differ only in what they charge for. A per-topic artifact
unlocks its topic, which buys every artifact of that topic at once; a
course-level one charges its own price and refunds it on every failure. Both
release what they took if the work does not arrive.

Two things are load-bearing here. Retrieval is narrowed to the documents the
plan's analysis was given, so a guide for "Graph Traversal" cannot quietly
answer from a course the student excluded from their exam scope. And a topic
must be one the plan actually ranked: a topic key that is merely well-formed
buys nothing, because the price of a topic is the price of the plan's topic.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
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
from services.credits import GENERATION_CREDIT_COSTS, ChargeReceipt, CreditService
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
    with_template_temperature,
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
class PlannedExam:
    """One whole plan, for the artifacts that draw on all of it at once."""

    plan_output_id: int
    analysis_output_id: int
    exam_date: date | None
    days_until_exam: int | None
    topics: tuple[PlannedTopic, ...]
    document_ids: tuple[UUID, ...]

    @property
    def retrieval_scope(self) -> tuple[UUID, ...] | None:
        return self.document_ids or None

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(topic.display_label for topic in self.topics)


@dataclass(frozen=True)
class ExamArtifactSpec:
    """Everything that differs between one per-topic artifact and another."""

    output_type: str
    generation_type: GenerationType
    prompt_template: str
    response_model: type[BaseModel]
    build_prompt: Callable[[str, object, PromptContext], str]
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
    prompt_version: str
    topic: PlannedTopic | None = None
    plan: PlannedExam | None = None
    unlock: TopicUnlock | None = None
    charge_receipt: ChargeReceipt | None = None

    @property
    def credits_charged(self) -> float:
        if self.unlock is not None:
            return self.unlock.amount
        if self.charge_receipt is not None:
            return self.charge_receipt.amount
        return 0.0


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

    @classmethod
    def resolve_plan(
        cls, db: Session, course_id: int, *, plan_output_id: int | None = None
    ) -> PlannedExam:
        """The whole plan, for the artifacts that draw on all of it at once.

        Topics come back in the order the plan ranked them, so an artifact that
        wants "the most important five" takes the first five rather than
        re-deriving a priority the plan already settled deterministically.
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
        analysis_output_id = stored.get("analysis_output_id")
        scope = _analysis_scope(db, analysis_output_id)
        entries = [
            entry for entry in stored.get("topics", []) if isinstance(entry, dict)
        ]
        entries.sort(key=lambda entry: entry.get("rank") or 0)

        topics = tuple(
            PlannedTopic(
                plan_output_id=plan.id,
                analysis_output_id=(
                    analysis_output_id if isinstance(analysis_output_id, int) else 0
                ),
                topic_key=str(entry.get("topic_key") or ""),
                display_label=str(
                    entry.get("display_label") or entry.get("topic_key") or ""
                ),
                rank=int(entry.get("rank") or 0),
                priority_band=str(entry.get("priority_band") or ""),
                is_high_priority=bool(entry.get("is_high_priority")),
                mastery_percentage=(
                    entry.get("mastery_percentage")
                    if isinstance(entry.get("mastery_percentage"), int)
                    else None
                ),
                document_ids=scope,
            )
            for entry in entries
            if entry.get("topic_key")
        )
        if not topics:
            raise ExamArtifactPlanMissingError(EXAM_PLAN_REQUIRED_MESSAGE)

        return PlannedExam(
            plan_output_id=plan.id,
            analysis_output_id=(
                analysis_output_id if isinstance(analysis_output_id, int) else 0
            ),
            exam_date=_as_date(stored.get("exam_date")),
            days_until_exam=(
                stored.get("days_until_exam")
                if isinstance(stored.get("days_until_exam"), int)
                else None
            ),
            topics=topics,
            document_ids=scope,
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

        with acquire_generation_locks(db, material.document_ids):
            prompt_context = resolve_prompt_context(db, course=course, user_id=user_id)
            prompt = spec.build_prompt(material.text, topic, prompt_context)

            metadata = None
            try:
                # The template's own declared temperature, applied to the call it was declared for.
                provider = with_template_temperature(
                    provider, PromptLoader.temperature_for(spec.prompt_template)
                )
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

    @classmethod
    def generate_for_plan(
        cls,
        db: Session,
        course_id: int,
        plan: PlannedExam,
        provider: TextGenerationProvider,
        *,
        user_id: int,
        spec: ExamArtifactSpec,
        price_key: str,
        query_subject: str,
    ) -> ExamArtifactGeneration:
        """Charge for one course-level artifact and produce it.

        This is the ordinary generation ladder: charge, retrieve, generate,
        validate, and refund on every branch that does not reach the end. It is
        separate from the per-topic path only because what it takes is a price
        rather than an unlock, and a price is refunded where an unlock is
        released.
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

        receipt = CreditService.charge(
            db, user_id, GENERATION_CREDIT_COSTS[price_key], source_type=price_key
        )
        if receipt is None:
            log_failure(ErrorCategory.INSUFFICIENT_CREDITS)
            raise InsufficientCreditsError("Insufficient credits.")

        query = build_retrieval_query(
            course, query_subject, suffix=spec.retrieval_query_suffix
        )

        try:
            material = cls.get_course_material(
                db,
                course_id,
                query=query,
                document_ids=plan.retrieval_scope,
                max_characters=spec.material_max_characters,
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
            acquire_generation_locks(db, material.document_ids),
        ):
            prompt_context = resolve_prompt_context(db, course=course, user_id=user_id)
            prompt = spec.build_prompt(material.text, plan, prompt_context)

            metadata = None
            try:
                # The template's own declared temperature, applied to the call it was declared for.
                provider = with_template_temperature(
                    provider, PromptLoader.temperature_for(spec.prompt_template)
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
                raise ExamArtifactError(spec.provider_failed_message) from exc
            except Exception:
                CreditService.refund(db, receipt)
                raise

            try:
                validated = spec.response_model.model_validate(result)
            except ValidationError as exc:
                CreditService.refund(db, receipt)
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
                prompt_version=PromptLoader.load_template(spec.prompt_template).version,
                plan=plan,
                charge_receipt=receipt,
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
        db: Session, course_id: int, output_type: str, *, topic_key: str | None = None
    ) -> GeneratedOutput | None:
        """The newest artifact of this kind, optionally for one topic.

        A topic is matched through the stored settings document rather than a
        column, because the topic key belongs to the artifact's own contract
        and ``generated_outputs`` stays a table of generations rather than a
        table of Exam Mode. Course-level artifacts pass no key and take the
        newest row.
        """
        rows = db.scalars(
            select(GeneratedOutput)
            .where(
                GeneratedOutput.course_id == course_id,
                GeneratedOutput.output_type == output_type,
            )
            .order_by(GeneratedOutput.created_at.desc(), GeneratedOutput.id.desc())
        ).all()
        if topic_key is None:
            return rows[0] if rows else None
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


def _as_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


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
