from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from schemas.generation import RetrievedContext


class SummaryFormat(str, Enum):
    OVERVIEW = "overview"
    COMPREHENSIVE = "comprehensive"
    KEY_CONCEPTS = "key_concepts"
    EXAM_TIPS = "exam_tips"


class SummaryLength(str, Enum):
    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"


class DetailLevel(str, Enum):
    BASIC = "basic"
    STANDARD = "standard"
    DETAILED = "detailed"


class SummaryMode(str, Enum):
    GENERAL = "general"
    EXAM_FOCUSED = "exam_focused"


class StudyGuideRequest(BaseModel):
    summary_format: SummaryFormat
    topic_focus: str = Field(min_length=1, max_length=200)
    summary_length: SummaryLength = SummaryLength.MEDIUM
    detail_level: DetailLevel = DetailLevel.STANDARD
    summary_mode: SummaryMode = SummaryMode.GENERAL
    model: str | None = Field(
        default=None,
        description="Explicit model override, or omit to use preferred/default model",
    )


class StudyGuideGenerationSettings(BaseModel):
    """The options a stored study guide was generated with.

    ``version`` and ``output_type`` make the persisted document self-describing,
    so other generated output types can share the same column later.
    """

    model_config = ConfigDict(extra="ignore")

    version: Literal[1] = 1
    output_type: Literal["study_guide"] = "study_guide"
    summary_format: SummaryFormat
    topic_focus: str
    summary_length: SummaryLength
    detail_level: DetailLevel
    summary_mode: SummaryMode
    retrieval_limit: int
    retrieval_min_similarity: float

    @classmethod
    def from_request(
        cls,
        request: "StudyGuideRequest",
        *,
        retrieval_limit: int,
        retrieval_min_similarity: float,
    ) -> "StudyGuideGenerationSettings":
        return cls(
            summary_format=request.summary_format,
            topic_focus=request.topic_focus,
            summary_length=request.summary_length,
            detail_level=request.detail_level,
            summary_mode=request.summary_mode,
            retrieval_limit=retrieval_limit,
            retrieval_min_similarity=retrieval_min_similarity,
        )


class StudyGuideGenerationContext(BaseModel):
    """What retrieval actually produced for a stored study guide."""

    model_config = ConfigDict(extra="ignore")

    version: Literal[1] = 1
    chunks_ranked: int
    chunks_retrieved: int
    chunks_used: int
    chunks_available: int
    lowest_similarity: float | None = None
    highest_similarity: float | None = None
    truncated: bool

    @classmethod
    def from_material(cls, material) -> "StudyGuideGenerationContext":
        return cls(
            chunks_ranked=material.chunks_ranked,
            chunks_retrieved=material.chunks_retrieved,
            chunks_used=material.chunks_used,
            chunks_available=material.chunks_available,
            lowest_similarity=material.lowest_similarity,
            highest_similarity=material.highest_similarity,
            truncated=material.truncated,
        )


class ImportantTerm(BaseModel):
    term: str
    definition: str


class CommonMistake(BaseModel):
    mistake: str
    correction: str


class ExamTips(BaseModel):
    lecture_based: list[str]
    ai_suggestions: list[str]


class Difficulty(BaseModel):
    level: Literal["Easy", "Medium", "Hard"]
    reason: str


class Coverage(BaseModel):
    status: Literal["Complete", "Mostly Complete", "Partial", "Limited"]
    estimated_completeness: int = Field(ge=0, le=100)


class StudyGuideResponse(BaseModel):
    title: str
    summary: str
    key_points: list[str]
    important_terms: list[ImportantTerm]
    common_mistakes: list[CommonMistake]
    exam_tips: ExamTips
    difficulty: Difficulty
    estimated_study_time: str
    prerequisites: list[str]
    learning_objectives: list[str]
    coverage: Coverage
    confidence_notes: str


class StudyGuideGenerationResult(RetrievedContext):
    study_guide: StudyGuideResponse
    generated_output_id: int
