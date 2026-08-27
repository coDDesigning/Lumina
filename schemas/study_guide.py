from enum import Enum
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from schemas.citation import (
    Citation,
    CitationKeys,
    MaybeCitedText,
    MaybeGeneratedCitedText,
)
from schemas.generation import RetrievalGenerationContext, RetrievedContext


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
    LAST_MINUTE = "last_minute"


# A last-minute review sheet is its own artifact rather than a study guide with a
# different tone: it is asked for separately, reopened separately, and listed
# separately, so it is stored under its own output type.
STUDY_GUIDE_OUTPUT_TYPE = "study_guide"
LAST_MINUTE_REVIEW_OUTPUT_TYPE = "last_minute_review"

OUTPUT_TYPE_BY_SUMMARY_MODE: dict[SummaryMode, str] = {
    SummaryMode.GENERAL: STUDY_GUIDE_OUTPUT_TYPE,
    SummaryMode.EXAM_FOCUSED: STUDY_GUIDE_OUTPUT_TYPE,
    SummaryMode.LAST_MINUTE: LAST_MINUTE_REVIEW_OUTPUT_TYPE,
}


def output_type_for(summary_mode: SummaryMode) -> str:
    """The ``generated_outputs.output_type`` one summary mode is stored under."""
    return OUTPUT_TYPE_BY_SUMMARY_MODE.get(summary_mode, STUDY_GUIDE_OUTPUT_TYPE)


class StudyGuideRequest(BaseModel):
    summary_format: SummaryFormat
    topic_focus: str = Field(min_length=1, max_length=200)
    summary_length: SummaryLength = SummaryLength.MEDIUM
    detail_level: DetailLevel = DetailLevel.STANDARD
    summary_mode: SummaryMode = SummaryMode.GENERAL
    use_profile_knowledge: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "use_profile_knowledge", "include_profile_context"
        ),
        description="Whether to include student profile knowledge context (opt-in)",
    )
    model: str | None = Field(
        default=None,
        description="Explicit model override, or omit to use preferred/default model",
    )

    @property
    def include_profile_context(self) -> bool:
        return self.use_profile_knowledge


class StudyGuideGenerationSettings(BaseModel):
    """The options a stored study guide was generated with.

    ``version`` and ``output_type`` make the persisted document self-describing,
    so other generated output types can share the same column later.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    version: Literal[1] = 1
    output_type: Literal["study_guide", "last_minute_review"] = "study_guide"
    summary_format: SummaryFormat
    topic_focus: str
    summary_length: SummaryLength
    detail_level: DetailLevel
    summary_mode: SummaryMode
    use_profile_knowledge: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "use_profile_knowledge", "include_profile_context"
        ),
    )
    retrieval_limit: int
    retrieval_min_similarity: float

    @property
    def include_profile_context(self) -> bool:
        return self.use_profile_knowledge

    @classmethod
    def from_request(
        cls,
        request: "StudyGuideRequest",
        *,
        retrieval_limit: int,
        retrieval_min_similarity: float,
    ) -> "StudyGuideGenerationSettings":
        summary_mode = (
            request.summary_mode
            if request.summary_mode is not None
            else SummaryMode.GENERAL
        )
        return cls(
            output_type=output_type_for(summary_mode),
            summary_format=request.summary_format
            if request.summary_format is not None
            else SummaryFormat.COMPREHENSIVE,
            topic_focus=request.topic_focus
            if request.topic_focus is not None
            else "All Topics",
            summary_length=request.summary_length
            if request.summary_length is not None
            else SummaryLength.MEDIUM,
            detail_level=request.detail_level
            if request.detail_level is not None
            else DetailLevel.STANDARD,
            summary_mode=summary_mode,
            use_profile_knowledge=request.use_profile_knowledge,
            retrieval_limit=retrieval_limit,
            retrieval_min_similarity=retrieval_min_similarity,
        )


class StudyGuideGenerationContext(RetrievalGenerationContext):
    """What retrieval actually produced for a stored study guide."""


class ImportantTerm(BaseModel):
    term: str
    definition: str
    citations: list[Citation] = []


class CommonMistake(BaseModel):
    mistake: str
    correction: str
    citations: list[Citation] = []


class ExamTips(BaseModel):
    lecture_based: list[MaybeCitedText]
    ai_suggestions: list[str]


class Difficulty(BaseModel):
    level: Literal["Easy", "Medium", "Hard"]
    reason: str


class Coverage(BaseModel):
    status: Literal["Complete", "Mostly Complete", "Partial", "Limited"]
    estimated_completeness: int = Field(ge=0, le=100)


class StudyGuideResponse(BaseModel):
    title: str
    summary: MaybeCitedText
    key_points: list[MaybeCitedText]
    important_terms: list[ImportantTerm]
    common_mistakes: list[CommonMistake]
    exam_tips: ExamTips
    difficulty: Difficulty
    estimated_study_time: str
    prerequisites: list[MaybeCitedText]
    learning_objectives: list[MaybeCitedText]
    coverage: Coverage
    confidence_notes: str


class GeneratedImportantTerm(BaseModel):
    model_config = ConfigDict(extra="ignore")

    term: str
    definition: str
    citations: CitationKeys = []


class GeneratedCommonMistake(BaseModel):
    model_config = ConfigDict(extra="ignore")

    mistake: str
    correction: str
    citations: CitationKeys = []


class GeneratedExamTips(BaseModel):
    model_config = ConfigDict(extra="ignore")

    lecture_based: list[MaybeGeneratedCitedText]
    ai_suggestions: list[str]


class GeneratedStudyGuideResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str
    summary: MaybeGeneratedCitedText
    key_points: list[MaybeGeneratedCitedText]
    important_terms: list[GeneratedImportantTerm]
    common_mistakes: list[GeneratedCommonMistake]
    exam_tips: GeneratedExamTips
    difficulty: Difficulty
    estimated_study_time: str
    prerequisites: list[MaybeGeneratedCitedText]
    learning_objectives: list[MaybeGeneratedCitedText]
    coverage: Coverage
    confidence_notes: str


class StudyGuideGenerationResult(RetrievedContext):
    study_guide: StudyGuideResponse
    generated_output_id: int
