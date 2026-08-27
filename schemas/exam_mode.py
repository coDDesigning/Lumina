"""Request, response, and persisted-document contracts for Exam Mode.

Three families live here. ``Generated*`` models validate raw provider JSON and
deliberately carry no score, rank, or ordering: the model reports what it read
and the application decides what matters. ``*Document`` models are the versioned
JSON written into ``generated_outputs``, written strictly and read back
permissively. Everything else is the HTTP surface.
"""

from datetime import date, datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from schemas.citation import Citation, CitationKeys
from schemas.generation import RetrievalGenerationContext, RetrievedContext
from schemas.study_guide import Coverage

MAX_SELECTED_DOCUMENTS = 50
MAX_DISCOVERED_TOPICS = 60
MAX_EXTRACTED_QUESTIONS = 200
MAX_SELECTED_TOPICS = 60
MAX_TOPIC_ALIASES = 12
MAX_QUESTION_SUBPARTS = 26
MAX_MARKING_POINTS = 20
MAX_QUESTION_VISUALS = 8
MAX_QUESTION_TOPICS = 6

DEFAULT_TOPIC_FOCUS = "All Topics"

SELECTION_MODE_MANUAL = "manual"
SELECTION_MODE_ALL_DISCOVERED = "all_discovered"

RANKING_ENGINE_DETERMINISTIC = "deterministic"


class ExamQuestionType(str, Enum):
    MULTIPLE_CHOICE = "multiple_choice"
    TRUE_FALSE = "true_false"
    SHORT_ANSWER = "short_answer"
    STRUCTURED = "structured"
    ESSAY = "essay"
    PROBLEM = "problem"
    PROOF = "proof"
    UNSPECIFIED = "unspecified"


class ExamDifficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class GeneratedTopicCandidate(BaseModel):
    """One candidate topic exactly as the provider reported it.

    There is no score, rank, priority, or ordering field, and ``extra`` is
    ignored, so a model that invents one has it discarded before it can reach
    the ranking engine.
    """

    model_config = ConfigDict(extra="ignore")

    label: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=MAX_TOPIC_ALIASES)
    in_syllabus: bool = False
    in_course_topics: bool = False
    in_past_exams: bool = False
    in_material: bool = False
    syllabus_weight_percent: float | None = Field(default=None, ge=0, le=100)
    syllabus_mention_count: int = Field(default=0, ge=0)
    material_chunk_count: int = Field(default=0, ge=0)
    material_character_count: int = Field(default=0, ge=0)
    discovery_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    citations: CitationKeys = []


class GeneratedExamSubpart(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: str = Field(default="", max_length=20)
    text: str = Field(min_length=1)
    marks: float | None = Field(default=None, ge=0)


class GeneratedVisualRef(BaseModel):
    """A pointer to an already-extracted visual, by stable position.

    Never a ``document_visuals`` identifier: reprocessing a document deletes
    and reinserts its pages, so those identifiers do not survive.
    """

    model_config = ConfigDict(extra="ignore")

    page_number: int | None = Field(default=None, ge=1)
    visual_index: int | None = Field(default=None, ge=0)
    visual_type: str = Field(default="other", max_length=20)


class GeneratedPastExamQuestion(BaseModel):
    model_config = ConfigDict(extra="ignore")

    question_label: str | None = Field(default=None, max_length=50)
    question_number: int | None = Field(default=None, ge=0)
    question_text: str = Field(min_length=1)
    subparts: list[GeneratedExamSubpart] = Field(
        default_factory=list, max_length=MAX_QUESTION_SUBPARTS
    )
    question_type: ExamQuestionType = ExamQuestionType.UNSPECIFIED
    difficulty: ExamDifficulty | None = None
    marks: float | None = Field(default=None, ge=0)
    answer_guidance: str | None = None
    marking_points: list[str] = Field(
        default_factory=list, max_length=MAX_MARKING_POINTS
    )
    visual_refs: list[GeneratedVisualRef] = Field(
        default_factory=list, max_length=MAX_QUESTION_VISUALS
    )
    topics: list[str] = Field(default_factory=list, max_length=MAX_QUESTION_TOPICS)
    citations: CitationKeys = []


class GeneratedExamAnalysisResponse(BaseModel):
    """The whole provider response for one source analysis.

    ``topics`` requires at least one entry, so a response that discovered
    nothing usable is an invalid structure rather than an empty analysis a
    later plan would rank into nothing.
    """

    model_config = ConfigDict(extra="ignore")

    topics: list[GeneratedTopicCandidate] = Field(
        min_length=1, max_length=MAX_DISCOVERED_TOPICS
    )
    past_exam_questions: list[GeneratedPastExamQuestion] = Field(
        default_factory=list, max_length=MAX_EXTRACTED_QUESTIONS
    )
    coverage: Coverage
    confidence_notes: str = ""


class ExamAnalysisRequest(BaseModel):
    document_ids: list[UUID] | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_SELECTED_DOCUMENTS,
        description=(
            "The course documents to analyse, or omit to analyse every ready "
            "document in the course"
        ),
    )
    topic_focus: str = Field(default=DEFAULT_TOPIC_FOCUS, min_length=1, max_length=200)
    model: str | None = Field(
        default=None,
        description="Explicit model override, or omit to use the preferred model",
    )

    @field_validator("document_ids")
    @classmethod
    def unique_document_ids(cls, value: list[UUID] | None) -> list[UUID] | None:
        if value is None:
            return None
        return list(dict.fromkeys(value))


class ExamPlanRequest(BaseModel):
    analysis_output_id: int | None = Field(
        default=None,
        description="The analysis to plan from, or omit to use the latest one",
    )
    selected_topic_keys: list[str] = Field(
        default_factory=list, max_length=MAX_SELECTED_TOPICS
    )
    high_priority_topic_keys: list[str] = Field(
        default_factory=list, max_length=MAX_SELECTED_TOPICS
    )
    selection_mode: Literal["manual", "all_discovered"] = SELECTION_MODE_MANUAL

    @field_validator("selected_topic_keys", "high_priority_topic_keys")
    @classmethod
    def unique_keys(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(key.strip() for key in value if key.strip()))

    @model_validator(mode="after")
    def priorities_are_selected(self) -> "ExamPlanRequest":
        selected = set(self.selected_topic_keys)
        unknown = [key for key in self.high_priority_topic_keys if key not in selected]
        if unknown:
            raise ValueError(
                "high_priority_topic_keys must be a subset of selected_topic_keys"
            )
        return self


class ExamSourceDocument(BaseModel):
    id: UUID
    label: str
    material_kind: str
    status: str
    is_past_exam: bool
    is_syllabus: bool


class ExamSourceInventory(BaseModel):
    """What this course could supply to an analysis, before one is run."""

    syllabus_present: bool
    syllabus_characters: int
    course_topics: list[str]
    documents: list[ExamSourceDocument]
    ready_document_count: int
    past_exam_document_count: int
    chunks_available: int


class ExamTopicCandidateView(BaseModel):
    topic_key: str
    display_label: str
    aliases: list[str] = []
    in_syllabus: bool = False
    in_course_topics: bool = False
    in_past_exams: bool = False
    in_material: bool = False
    discovery_confidence: float = 0.5
    syllabus_weight_percent: float | None = None
    syllabus_mention_count: int = 0
    past_exam_question_count: int = 0
    material_chunk_count: int = 0
    citations: list[Citation] = []


class ExamQuestionView(BaseModel):
    position: int
    document_id: UUID | None = None
    page_start: int | None = None
    page_end: int | None = None
    question_label: str | None = None
    question_number: int | None = None
    question_text: str
    subparts: list[dict[str, Any]] = []
    question_type: str
    difficulty: str | None = None
    marks: float | None = None
    answer_guidance: str | None = None
    marking_points: list[str] = []
    visual_refs: list[dict[str, Any]] = []
    topic_key: str | None = None
    topic_mappings: list[dict[str, Any]] = []
    citations: list[Citation] = []


class ExamQuestionPage(BaseModel):
    analysis_output_id: int
    total: int
    limit: int
    offset: int
    questions: list[ExamQuestionView]


class ExamSelectionCarryOver(BaseModel):
    """What a previous plan's choices mean against a newer analysis.

    Read-only. A rescan never re-selects anything on the student's behalf; it
    reports what still matches so the student can confirm.
    """

    previous_plan_output_id: int | None = None
    preselected_topic_keys: list[str] = []
    high_priority_topic_keys: list[str] = []
    new_topic_keys: list[str] = []
    unsupported_topic_keys: list[str] = []


class ExamAnalysisView(BaseModel):
    generated_output_id: int
    created_at: datetime
    model_used: str | None = None
    candidate_count: int
    past_exam_question_count: int
    documents_analysed: list[UUID] = []
    manual_review_recommended: bool = True
    topics: list[ExamTopicCandidateView] = []
    selection_carry_over: ExamSelectionCarryOver = ExamSelectionCarryOver()
    coverage: dict[str, Any] | None = None
    confidence_notes: str = ""


class ExamAnalysisResult(RetrievedContext):
    analysis: ExamAnalysisView


class ExamPlanTopicView(BaseModel):
    topic_key: str
    display_label: str
    rank: int
    is_high_priority: bool = False
    priority_score: int
    priority_band: str
    has_any_evidence: bool = True
    is_unattempted: bool = False
    mastery_percentage: int | None = None
    signals: dict[str, Any] = {}
    reason_codes: list[str] = []
    explanation: str = ""


class ExamPlanStaleness(BaseModel):
    is_stale: bool = False
    requires_rescan: bool = False
    stale_reasons: list[str] = []


class ExamPlanView(BaseModel):
    generated_output_id: int
    analysis_output_id: int
    plan_version: int
    supersedes_output_id: int | None = None
    created_at: datetime
    exam_date: date | None = None
    days_until_exam: int | None = None
    selection_mode: str = SELECTION_MODE_MANUAL
    manual_review_recommended: bool = True
    ranking_engine: str = RANKING_ENGINE_DETERMINISTIC
    ranking_policy_version: int = 1
    configured_weights: dict[str, int] = {}
    effective_weights: dict[str, int] = {}
    signals_available: dict[str, bool] = {}
    signal_bases: dict[str, str] = {}
    unmapped_mastery_labels: int = 0
    warnings: list[str] = []
    topics: list[ExamPlanTopicView] = []
    staleness: ExamPlanStaleness = ExamPlanStaleness()


class ExamPlanSummary(BaseModel):
    generated_output_id: int
    analysis_output_id: int
    plan_version: int
    supersedes_output_id: int | None = None
    created_at: datetime
    exam_date: date | None = None
    topic_count: int
    selection_mode: str = SELECTION_MODE_MANUAL
    is_current: bool = False


class ExamPlanList(BaseModel):
    plans: list[ExamPlanSummary] = []
    current_plan_output_id: int | None = None


class ExamAnalysisGenerationSettings(BaseModel):
    """The options one stored source analysis was produced with."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    version: Literal[1] = 1
    output_type: Literal["exam_topic_analysis"] = "exam_topic_analysis"
    topic_focus: str = DEFAULT_TOPIC_FOCUS
    rescan: bool = False
    document_ids_requested: list[UUID] = []
    retrieval_limit: int
    retrieval_min_similarity: float
    material_max_characters: int
    topic_key_version: int
    prompt_template: str
    prompt_version: str


class ExamAnalysisGenerationContext(RetrievalGenerationContext):
    """What retrieval and extraction actually produced for one analysis."""

    documents_analysed: list[UUID] = []
    past_exam_documents_analysed: list[UUID] = []
    candidates_discovered: int = 0
    questions_extracted: int = 0
    course_topics_promoted: int = 0


class ExamAnalysisSummaryDocument(BaseModel):
    """The ``generated_outputs.content`` payload of one analysis.

    The candidates and the extracted questions are rows, not JSON: this
    document records only what the run as a whole produced, so a history read
    stays small and the queryable evidence stays queryable.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    version: Literal[1] = 1
    output_type: Literal["exam_topic_analysis"] = "exam_topic_analysis"
    candidate_count: int
    past_exam_question_count: int
    documents_analysed: list[UUID] = []
    past_exam_documents_analysed: list[UUID] = []
    syllabus_present: bool = False
    course_topics_promoted: int = 0
    manual_review_recommended: bool = True
    coverage: Coverage | None = None
    confidence_notes: str = ""


class ExamPlanGenerationSettings(BaseModel):
    """The request one stored exam plan was produced from."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    version: Literal[1] = 1
    output_type: Literal["exam_plan"] = "exam_plan"
    analysis_output_id: int
    selected_topic_keys: list[str] = []
    high_priority_topic_keys: list[str] = []
    selection_mode: str = SELECTION_MODE_MANUAL
    manual_review_recommended: bool = True
    ranking_policy_version: int
    topic_key_version: int


class ExamPlanGenerationContext(BaseModel):
    """What the deterministic ranking actually consumed.

    ``ranking_engine`` is recorded explicitly because no model produced this
    row. ``generated_outputs.model_used`` stays null for a plan, and the model
    attribution of the analysis it was built from lives on that analysis.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    version: Literal[1] = 1
    ranking_engine: Literal["deterministic"] = RANKING_ENGINE_DETERMINISTIC
    ranking_policy_version: int
    analysis_output_id: int
    analysis_model_used: str | None = None
    analysis_created_at: datetime | None = None
    candidates_available: int = 0
    topics_ranked: int = 0
    unmapped_mastery_labels: int = 0
    configured_weights: dict[str, int] = {}
    effective_weights: dict[str, int] = {}
    signals_available: dict[str, bool] = {}
    signal_bases: dict[str, str] = {}


class ExamPlanFingerprint(BaseModel):
    """The inputs one plan was built from, for read-time staleness only.

    ``mastery_user_id`` is stored rather than assumed. Plan reads follow the
    administrator read-any policy, so recomputing mastery against the reader
    would compare an owner's plan to an administrator's empty history and call
    it changed on every single read.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    version: Literal[1] = 1
    mastery_user_id: int
    analysis_output_id: int
    exam_date: date | None = None
    syllabus_digest: str | None = None
    course_topic_keys: list[str] = []
    ready_document_ids: list[str] = []
    past_exam_document_ids: list[str] = []
    document_revision_digest: str | None = None
    graded_answer_count: int = 0
    mastery_digest: str | None = None
    selected_topic_keys: list[str] = []
    high_priority_topic_keys: list[str] = []
    ranking_policy_version: int
    topic_key_version: int


class ExamPlanDocument(BaseModel):
    """The ``generated_outputs.content`` payload of one exam plan.

    Immutable. A later plan supersedes this one by reference; nothing ever
    rewrites it, which is what lets a student reopen the reasoning they
    actually studied from.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    version: Literal[1] = 1
    output_type: Literal["exam_plan"] = "exam_plan"
    plan_version: int
    supersedes_output_id: int | None = None
    analysis_output_id: int
    exam_date: date | None = None
    days_until_exam: int | None = None
    selection_mode: str = SELECTION_MODE_MANUAL
    manual_review_recommended: bool = True
    ranking_engine: Literal["deterministic"] = RANKING_ENGINE_DETERMINISTIC
    ranking_policy_version: int
    configured_weights: dict[str, int] = {}
    effective_weights: dict[str, int] = {}
    signals_available: dict[str, bool] = {}
    signal_bases: dict[str, str] = {}
    unmapped_mastery_labels: int = 0
    warnings: list[str] = []
    topics: list[dict[str, Any]] = []
    fingerprint: ExamPlanFingerprint


class ExamPlanCreationResult(BaseModel):
    plan: ExamPlanView
