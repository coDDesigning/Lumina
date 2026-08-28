"""The day-by-day exam roadmap, its request, and its persisted documents.

A roadmap is a scheduling artifact rather than a generated one: no text model
writes any part of it, so the response is exactly what the algorithm decided and
a reopened row renders it again without reaching a provider.
"""

from datetime import date
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from schemas.citation import Citation


class RoadmapDayKind(str, Enum):
    """What one scheduled day is for."""

    STUDY = "study"
    REVIEW = "review"
    FINAL_REVIEW = "final_review"
    LAST_MINUTE = "last_minute"


class RoadmapHorizon(str, Enum):
    """How much time the plan had to work with.

    ``ZERO_DAY`` and ``ONE_DAY`` are the triage horizons: they produce a valid
    last-minute plan rather than an error. ``LONG`` means the plan was capped and
    starts after today.
    """

    ZERO_DAY = "zero_day"
    ONE_DAY = "one_day"
    STANDARD = "standard"
    LONG = "long"


class TopicSource(str, Enum):
    SYLLABUS = "syllabus"
    QUIZ = "quiz"
    EXAM_PLAN = "exam_plan"


class TopicMaterialStatus(str, Enum):
    """Why a scheduled topic does or does not name course material.

    A topic without material is scheduled anyway and says why, because a plan
    that hides a gap is worse than one that names it.
    """

    RESOLVED = "resolved"
    NO_MATCH = "no_match"
    NOT_INDEXED = "not_indexed"
    NO_MATERIAL = "no_material"
    NOT_REQUESTED = "not_requested"


class DeferralReason(str, Enum):
    HORIZON_TOO_SHORT = "horizon_too_short"


class ExamRoadmapRequest(BaseModel):
    """Options for one roadmap generation.

    The exam date is deliberately not a request field: the course owns it, and a
    plan a student can act on is the one their course date produces.

    ``plan_output_id`` names an Exam Mode plan to schedule instead of the
    course's declared topics. The plan's own ranking is preserved rather than
    recomputed, because a student who reviewed and prioritised those topics has
    already decided what matters and a second engine disagreeing with the first
    would be two answers to one question.
    """

    plan_output_id: int | None = Field(
        default=None,
        ge=1,
        description=(
            "The exam plan whose ranked topics to schedule, or omit to schedule "
            "the course's own declared topics"
        ),
    )
    max_topics_per_day: int = Field(default=3, ge=1, le=6)
    include_materials: bool = Field(
        default=True,
        description=(
            "Resolve course material and citations for each scheduled topic. "
            "Disabling it skips retrieval entirely."
        ),
    )


class RoadmapMaterial(BaseModel):
    """One document a scheduled topic should be studied from.

    Denormalized like a citation, and for the same reason: the reference must
    still resolve after the document it names is deleted, so it is never a link.
    """

    model_config = ConfigDict(extra="ignore")

    document_id: UUID
    document_label: str
    page_start: int | None = None
    page_end: int | None = None


class RankedTopicView(BaseModel):
    """One entry of the ranked plan the schedule consumed."""

    model_config = ConfigDict(extra="ignore")

    topic: str
    topic_key: str | None = None
    source: TopicSource
    syllabus_position: int | None = None
    importance: float
    mastery_percentage: int | None = None
    questions_answered: int | None = 0
    priority: float


class RoadmapTopic(BaseModel):
    """One topic on one day, with the goal and the sources it is studied from."""

    model_config = ConfigDict(extra="ignore")

    topic: str
    topic_key: str | None = None
    goal: str
    pass_number: int = Field(ge=1)
    source: TopicSource
    syllabus_position: int | None = None
    importance: float
    mastery_percentage: int | None = None
    questions_answered: int | None = 0
    priority: float
    material_status: TopicMaterialStatus
    materials: list[RoadmapMaterial] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)


class RoadmapDay(BaseModel):
    model_config = ConfigDict(extra="ignore")

    day_index: int = Field(ge=1)
    date: date
    kind: RoadmapDayKind
    is_exam_day: bool = False
    focus: str
    topics: list[RoadmapTopic] = Field(default_factory=list)


class DeferredTopic(BaseModel):
    model_config = ConfigDict(extra="ignore")

    topic: str
    priority: float
    reason: DeferralReason


class ExamRoadmap(BaseModel):
    """One persisted roadmap version, self-describing on the way back out."""

    model_config = ConfigDict(extra="ignore")

    version: Literal[1] = 1
    output_type: Literal["exam_roadmap"] = "exam_roadmap"
    course_id: int
    exam_date: date
    generated_on: date
    starts_on: date
    days_until_exam: int
    scheduled_days: int
    lead_in_days: int = 0
    horizon: RoadmapHorizon
    materials_available: bool
    attempts_considered: int = 0
    roadmap_version: int = Field(ge=1)
    adapted_from_output_id: int | None = None
    plan_output_id: int | None = None
    ranked_topics: list[RankedTopicView] = Field(default_factory=list)
    days: list[RoadmapDay] = Field(default_factory=list)
    deferred_topics: list[DeferredTopic] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ExamRoadmapGenerationSettings(BaseModel):
    """The options one stored roadmap was generated with."""

    model_config = ConfigDict(extra="ignore")

    version: Literal[1] = 1
    output_type: Literal["exam_roadmap"] = "exam_roadmap"
    exam_date: date
    generated_on: date
    max_topics_per_day: int
    review_topics_per_day: int
    include_materials: bool
    retrieval_limit: int
    retrieval_min_similarity: float
    roadmap_version: int
    adapted_from_output_id: int | None = None
    plan_output_id: int | None = None


class ExamRoadmapGenerationContext(BaseModel):
    """What ranking, scheduling, and retrieval actually produced for one roadmap."""

    model_config = ConfigDict(extra="ignore")

    version: Literal[1] = 1
    topics_ranked: int
    topics_scheduled: int
    topics_deferred: int
    topics_with_materials: int
    mastery_topics: int
    attempts_considered: int
    scheduled_days: int
    citations_supplied: int = 0


class ExamRoadmapResult(BaseModel):
    roadmap: ExamRoadmap
    generated_output_id: int
