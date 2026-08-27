"""Persisting a roadmap: the database half of a deterministic, free schedule.

``services/exam_roadmap.py`` does the arithmetic and touches nothing. This reads
the plan, hands it over, and writes the answer, so the pure module can stay pure
and a test can keep proving it.

Costs nothing and calls nothing. ``model_used`` is null on the stored row, and
that is a truth claim rather than a gap: Python produced it, and the model that
produced the evidence is credited on the analysis the plan names — the same
arrangement the exam plan itself uses.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.app.models import OUTPUT_TYPE_EXAM_ROADMAP, GeneratedOutput
from schemas.exam_mode import ExamRoadmapDocument
from services.exam_artifacts import ExamArtifactService, PlannedExam
from services.exam_roadmap import (
    ROADMAP_VERSION,
    Roadmap,
    RoadmapTopic,
    build_roadmap,
    resolve_day_count,
)
from services.generated_output import GeneratedOutputService


@dataclass(frozen=True)
class PersistedRoadmap:
    output: GeneratedOutput
    document: ExamRoadmapDocument


class ExamStudyRoadmapService:
    @staticmethod
    def build(plan: PlannedExam, *, requested_days: int | None = None) -> Roadmap:
        return build_roadmap(
            [
                RoadmapTopic(
                    topic_key=topic.topic_key,
                    display_label=topic.display_label,
                    rank=topic.rank,
                    priority_band=topic.priority_band,
                    is_high_priority=topic.is_high_priority,
                )
                for topic in plan.topics
            ],
            day_count=resolve_day_count(plan.days_until_exam, requested_days),
        )

    @staticmethod
    def document(plan: PlannedExam, roadmap: Roadmap) -> ExamRoadmapDocument:
        return ExamRoadmapDocument(
            plan_output_id=plan.plan_output_id,
            roadmap_version=ROADMAP_VERSION,
            exam_date=plan.exam_date,
            days_until_exam=plan.days_until_exam,
            day_count=roadmap.day_count,
            topic_count=roadmap.topic_count,
            days=[
                {
                    "day": day.day,
                    "label": day.label,
                    "title": day.title,
                    "focus": day.focus,
                    "is_review": day.is_review,
                    "topics": [
                        {
                            "topic_key": topic.topic_key,
                            "display_label": topic.display_label,
                            "rank": topic.rank,
                            "priority_band": topic.priority_band,
                            "is_high_priority": topic.is_high_priority,
                        }
                        for topic in day.topics
                    ],
                }
                for day in roadmap.days
            ],
            unscheduled_topic_keys=[
                topic.topic_key for topic in roadmap.unscheduled_topics
            ],
        )

    @classmethod
    def create(
        cls,
        db: Session,
        course_id: int,
        plan: PlannedExam,
        *,
        user_id: int,
        requested_days: int | None = None,
    ) -> PersistedRoadmap:
        roadmap = cls.build(plan, requested_days=requested_days)
        document = cls.document(plan, roadmap)
        output = GeneratedOutputService.record(
            db,
            course_id=course_id,
            user_id=user_id,
            output_type=OUTPUT_TYPE_EXAM_ROADMAP,
            content=document.model_dump_json(),
            model_used=None,
            generation_settings=None,
            generation_context=None,
        )
        return PersistedRoadmap(output=output, document=document)

    @staticmethod
    def latest(db: Session, course_id: int) -> GeneratedOutput | None:
        return ExamArtifactService.latest(db, course_id, OUTPUT_TYPE_EXAM_ROADMAP)
