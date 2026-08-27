"""Exam roadmaps: the planning consumer of a course's exam date.

The roadmap is the one generated artifact no text model writes. Ranking lives in
``services/exam_topic_ranking.py``, allocation in ``services/exam_schedule.py``,
and this module is the seam between them and the database: it reads the course's
declared topics and the mastery the quiz history already produced, resolves the
course material each scheduled topic should be studied from, and stores the
result through ``GeneratedOutputService`` like every other generated output.

Two consequences follow from there being no provider call. A roadmap costs no
credit, because there is nothing metered to charge for; and ``model_used`` is
stored as null, which is the truthful value for a row no model produced rather
than a gap waiting to be backfilled.

Retrieval is enrichment here, not substance. A topic the course material does
not answer is still scheduled and says so, because a plan that refuses to exist
over one unmatched topic is worse than a plan that names the gap. An embedding
or vector-store *failure* is different: it is transient and would silently
produce a material-less plan that looks permanent, so it fails the request.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models import Course, GeneratedOutput
from schemas.citation import Citation
from schemas.exam_roadmap import (
    DeferralReason,
    DeferredTopic,
    ExamRoadmap,
    ExamRoadmapGenerationContext,
    ExamRoadmapGenerationSettings,
    ExamRoadmapRequest,
    RankedTopicView,
    RoadmapDay,
    RoadmapMaterial,
    RoadmapTopic,
    TopicMaterialStatus,
)
from services.citations import resolve_citations
from services.course_material import count_available_chunks
from services.exam_schedule import (
    REVIEW_TOPICS_PER_DAY,
    Schedule,
    build_schedule,
)
from services.exam_topic_ranking import RankedTopic, rank_topics
from services.generated_output import GeneratedOutputService
from services.quiz_attempt import UNTAGGED_TOPIC, QuizAttemptService
from services.retrieval_material import (
    MaterialNotIndexedError,
    NoRelevantMaterialError,
    load_retrieved_material,
)
from services.retrieval_query import build_retrieval_query
from utils.ai_errors import (
    ExamDatePassedError,
    ExamDateRequiredError,
    ExamTopicsRequiredError,
    EXAM_DATE_PASSED_MESSAGE,
    EXAM_DATE_REQUIRED_MESSAGE,
    EXAM_TOPICS_REQUIRED_MESSAGE,
)

OUTPUT_TYPE = "exam_roadmap"

# One topic needs a handful of passages to name where it is studied from, not a
# prompt's worth of material: the assembled text is discarded and only the
# citations survive.
TOPIC_MATERIAL_CHUNK_LIMIT = 4

NO_MATERIAL_NOTE = (
    "This course has no processed material yet, so the plan names goals but no sources."
)


@dataclass(frozen=True)
class TopicMaterial:
    status: TopicMaterialStatus
    citations: tuple[Citation, ...] = ()
    materials: tuple[RoadmapMaterial, ...] = ()


@dataclass(frozen=True)
class ExamRoadmapGeneration:
    roadmap: ExamRoadmap
    applied_settings: ExamRoadmapGenerationSettings
    applied_context: ExamRoadmapGenerationContext


def _documents_from(citations: Sequence[Citation]) -> tuple[RoadmapMaterial, ...]:
    """Collapse a topic's citations into the documents it should be read from."""
    grouped: dict[UUID, RoadmapMaterial] = {}
    for citation in citations:
        existing = grouped.get(citation.document_id)
        start = citation.page_start
        end = (
            citation.page_end if citation.page_end is not None else citation.page_start
        )
        if existing is None:
            grouped[citation.document_id] = RoadmapMaterial(
                document_id=citation.document_id,
                document_label=citation.document_label,
                page_start=start,
                page_end=end,
            )
            continue
        pages = [
            page
            for page in (existing.page_start, existing.page_end, start, end)
            if page is not None
        ]
        grouped[citation.document_id] = RoadmapMaterial(
            document_id=existing.document_id,
            document_label=existing.document_label,
            page_start=min(pages) if pages else None,
            page_end=max(pages) if pages else None,
        )
    return tuple(grouped.values())


class ExamRoadmapService:
    @staticmethod
    def _mastery(db: Session, course_id: int, *, user_id: int):
        """The topic mastery and attempt count this student's history supports."""
        progress = QuizAttemptService.get_course_progress(
            db, course_id, user_id=user_id
        )
        measured = [
            entry for entry in progress.topic_mastery if entry.topic != UNTAGGED_TOPIC
        ]
        return measured, progress.attempts_count

    @classmethod
    def rank(
        cls, db: Session, course: Course, *, user_id: int
    ) -> tuple[list[RankedTopic], int, int]:
        """Rank the course's topics, newest quiz results included."""
        measured, attempts = cls._mastery(db, course.id, user_id=user_id)
        ranked = rank_topics(syllabus_topics=course.topics, mastery=measured)
        if not ranked:
            raise ExamTopicsRequiredError(EXAM_TOPICS_REQUIRED_MESSAGE)
        return ranked, len(measured), attempts

    @staticmethod
    def resolve_topic_material(
        db: Session, course: Course, topic: str, *, has_material: bool
    ) -> TopicMaterial:
        """Name the course material one topic should be studied from.

        A relevance miss and an indexing gap are reported rather than raised: the
        plan is still valid without them, and the two are kept apart because the
        remedies differ.
        """
        if not has_material:
            return TopicMaterial(status=TopicMaterialStatus.NO_MATERIAL)

        query = build_retrieval_query(course, topic)
        try:
            material = load_retrieved_material(
                db,
                course.id,
                query=query,
                limit=TOPIC_MATERIAL_CHUNK_LIMIT,
                min_similarity=settings.retrieval_min_similarity,
                max_characters=settings.study_guide_material_max_chars,
                include_citations=True,
            )
        except MaterialNotIndexedError:
            return TopicMaterial(status=TopicMaterialStatus.NOT_INDEXED)
        except NoRelevantMaterialError:
            return TopicMaterial(status=TopicMaterialStatus.NO_MATCH)

        citations = tuple(
            resolve_citations(
                [citation.key for citation in material.citations],
                material.citation_map,
            )
        )
        if not citations:
            return TopicMaterial(status=TopicMaterialStatus.NO_MATCH)
        return TopicMaterial(
            status=TopicMaterialStatus.RESOLVED,
            citations=citations,
            materials=_documents_from(citations),
        )

    @classmethod
    def _materials_for(
        cls,
        db: Session,
        course: Course,
        topics: Iterable[str],
        *,
        include_materials: bool,
        has_material: bool,
    ) -> dict[str, TopicMaterial]:
        """Resolve each distinct scheduled topic once, however often it recurs."""
        if not include_materials:
            return {}
        resolved: dict[str, TopicMaterial] = {}
        for topic in topics:
            if topic in resolved:
                continue
            resolved[topic] = cls.resolve_topic_material(
                db, course, topic, has_material=has_material
            )
        return resolved

    @staticmethod
    def _previous_roadmap_ids(
        db: Session, course_id: int, *, user_id: int
    ) -> tuple[int, int | None]:
        """How many roadmaps this student already has, and the latest one's id.

        A regeneration never edits an earlier plan: it counts them, points at the
        one it supersedes, and is written as a new row.
        """
        count = (
            db.scalar(
                select(func.count(GeneratedOutput.id)).where(
                    GeneratedOutput.course_id == course_id,
                    GeneratedOutput.user_id == user_id,
                    GeneratedOutput.output_type == OUTPUT_TYPE,
                )
            )
            or 0
        )
        latest = db.scalar(
            select(GeneratedOutput.id)
            .where(
                GeneratedOutput.course_id == course_id,
                GeneratedOutput.user_id == user_id,
                GeneratedOutput.output_type == OUTPUT_TYPE,
            )
            .order_by(GeneratedOutput.created_at.desc(), GeneratedOutput.id.desc())
            .limit(1)
        )
        return count, latest

    @staticmethod
    def _topic_view(
        scheduled, material: TopicMaterial, *, include_materials: bool
    ) -> RoadmapTopic:
        topic = scheduled.topic
        return RoadmapTopic(
            topic=topic.topic,
            goal=scheduled.goal,
            pass_number=scheduled.pass_number,
            source=topic.source,
            syllabus_position=topic.syllabus_position,
            importance=topic.importance,
            mastery_percentage=topic.mastery_percentage,
            questions_answered=topic.questions_answered,
            priority=topic.priority,
            material_status=(
                material.status
                if include_materials
                else TopicMaterialStatus.NOT_REQUESTED
            ),
            materials=list(material.materials),
            citations=list(material.citations),
        )

    @classmethod
    def generate(
        cls,
        db: Session,
        course_id: int,
        request: ExamRoadmapRequest,
        *,
        user_id: int,
        today: date | None = None,
    ) -> ExamRoadmapGeneration:
        """Build one roadmap version from the course's exam date and topics."""
        course = db.get(Course, course_id)
        if course is None or course.exam_date is None:
            raise ExamDateRequiredError(EXAM_DATE_REQUIRED_MESSAGE)

        current_day = today if today is not None else date.today()
        if course.exam_date < current_day:
            raise ExamDatePassedError(EXAM_DATE_PASSED_MESSAGE)

        ranked, mastery_topics, attempts = cls.rank(db, course, user_id=user_id)
        schedule: Schedule = build_schedule(
            ranked,
            today=current_day,
            exam_date=course.exam_date,
            max_topics_per_day=request.max_topics_per_day,
        )

        has_material = count_available_chunks(db, course_id) > 0
        materials = cls._materials_for(
            db,
            course,
            (
                scheduled.topic.topic
                for day in schedule.days
                for scheduled in day.topics
            ),
            include_materials=request.include_materials,
            has_material=has_material,
        )
        missing = TopicMaterial(status=TopicMaterialStatus.NO_MATERIAL)

        days = [
            RoadmapDay(
                day_index=index,
                date=day.date,
                kind=day.kind,
                is_exam_day=day.is_exam_day,
                focus=day.focus,
                topics=[
                    cls._topic_view(
                        scheduled,
                        materials.get(scheduled.topic.topic, missing),
                        include_materials=request.include_materials,
                    )
                    for scheduled in day.topics
                ],
            )
            for index, day in enumerate(schedule.days, start=1)
        ]

        notes = list(schedule.notes)
        if request.include_materials and not has_material:
            notes.insert(0, NO_MATERIAL_NOTE)

        previous_count, previous_id = cls._previous_roadmap_ids(
            db, course_id, user_id=user_id
        )

        roadmap = ExamRoadmap(
            course_id=course_id,
            exam_date=course.exam_date,
            generated_on=current_day,
            starts_on=schedule.starts_on,
            days_until_exam=schedule.days_until_exam,
            scheduled_days=len(days),
            lead_in_days=schedule.lead_in_days,
            horizon=schedule.horizon,
            materials_available=has_material,
            attempts_considered=attempts,
            roadmap_version=previous_count + 1,
            adapted_from_output_id=previous_id,
            ranked_topics=[
                RankedTopicView(
                    topic=topic.topic,
                    source=topic.source,
                    syllabus_position=topic.syllabus_position,
                    importance=topic.importance,
                    mastery_percentage=topic.mastery_percentage,
                    questions_answered=topic.questions_answered,
                    priority=topic.priority,
                )
                for topic in ranked
            ],
            days=days,
            deferred_topics=[
                DeferredTopic(
                    topic=topic.topic,
                    priority=topic.priority,
                    reason=DeferralReason.HORIZON_TOO_SHORT,
                )
                for topic in schedule.deferred
            ],
            notes=notes,
        )

        scheduled_topics = {
            scheduled.topic.topic for day in schedule.days for scheduled in day.topics
        }
        return ExamRoadmapGeneration(
            roadmap=roadmap,
            applied_settings=ExamRoadmapGenerationSettings(
                exam_date=course.exam_date,
                generated_on=current_day,
                max_topics_per_day=request.max_topics_per_day,
                review_topics_per_day=REVIEW_TOPICS_PER_DAY,
                include_materials=request.include_materials,
                retrieval_limit=TOPIC_MATERIAL_CHUNK_LIMIT,
                retrieval_min_similarity=settings.retrieval_min_similarity,
                roadmap_version=roadmap.roadmap_version,
                adapted_from_output_id=previous_id,
            ),
            applied_context=ExamRoadmapGenerationContext(
                topics_ranked=len(ranked),
                topics_scheduled=len(scheduled_topics),
                topics_deferred=len(schedule.deferred),
                topics_with_materials=sum(
                    1
                    for material in materials.values()
                    if material.status is TopicMaterialStatus.RESOLVED
                ),
                mastery_topics=mastery_topics,
                attempts_considered=attempts,
                scheduled_days=len(days),
                citations_supplied=sum(
                    len(material.citations) for material in materials.values()
                ),
            ),
        )

    @staticmethod
    def save_generated_output(
        db: Session,
        course_id: int,
        generation: ExamRoadmapGeneration,
        *,
        user_id: int,
    ) -> GeneratedOutput:
        """Persist one roadmap version; earlier versions are never rewritten."""
        return GeneratedOutputService.record(
            db,
            course_id=course_id,
            user_id=user_id,
            output_type=OUTPUT_TYPE,
            content=generation.roadmap.model_dump_json(),
            model_used=None,
            generation_settings=generation.applied_settings.model_dump_json(),
            generation_context=generation.applied_context.model_dump_json(),
        )
