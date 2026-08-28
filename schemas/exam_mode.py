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

from schemas.citation import (
    Citation,
    CitationKeys,
    MaybeCitedText,
    MaybeGeneratedCitedText,
)
from schemas.generation import RetrievalGenerationContext, RetrievedContext
from schemas.quiz import (
    MAX_QUIZ_QUESTIONS,
    MAX_TITLE_CHARS,
    MIN_QUIZ_QUESTIONS,
    GeneratedQuizQuestion,
    QuizQuestionType,
    QuizView,
)
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
MAX_GUIDE_SECTIONS = 12
MAX_GUIDE_ITEMS = 12
MAX_SUMMARY_POINTS = 10
MAX_SIMILAR_QUESTIONS = 20

# Bounds a student may set a paper's clock within. Shared by the request schema
# and its tests, so the two cannot disagree about what a valid sitting is.
MIN_MOCK_EXAM_MINUTES = 5
MAX_MOCK_EXAM_MINUTES = 360
DEFAULT_MOCK_EXAM_MINUTES = 60
MAX_REVIEW_TOPICS = 60
MAX_REVIEW_ITEMS = 12

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
    the ranking engine. There is no ``in_past_exams`` field either: past-exam
    evidence is counted from the questions already extracted from the papers,
    which is evidence a reader can go and check.
    """

    model_config = ConfigDict(extra="ignore")

    label: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=MAX_TOPIC_ALIASES)
    in_syllabus: bool = False
    in_course_topics: bool = False
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


class GeneratedPastExamExtraction(BaseModel):
    """The whole provider response for one past paper.

    ``questions`` may be empty, and that is a real answer rather than a
    failure: a document the student tagged as a past exam may turn out not to
    be one, and inventing a question would be the worse outcome.
    """

    model_config = ConfigDict(extra="ignore")

    questions: list[GeneratedPastExamQuestion] = Field(
        default_factory=list, max_length=MAX_EXTRACTED_QUESTIONS
    )
    confidence_notes: str = ""


class GeneratedExamAnalysisResponse(BaseModel):
    """The whole provider response for one source analysis.

    Topic discovery only. The questions in a past paper are read once, when
    the paper is uploaded, so there is no field here for the model to report
    one and no way for two analyses of one paper to disagree about it.

    ``topics`` requires at least one entry, so a response that discovered
    nothing usable is an invalid structure rather than an empty analysis a
    later plan would rank into nothing.
    """

    model_config = ConfigDict(extra="ignore")

    topics: list[GeneratedTopicCandidate] = Field(
        min_length=1, max_length=MAX_DISCOVERED_TOPICS
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
    document_id: UUID
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
    document_ids: list[UUID] = []
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


# --------------------------------------------------------------- per-topic study


class GeneratedTopicSection(BaseModel):
    """One section of a per-topic study guide, as the provider reported it."""

    model_config = ConfigDict(extra="ignore")

    heading: str = Field(min_length=1, max_length=200)
    body: MaybeGeneratedCitedText
    key_points: list[MaybeGeneratedCitedText] = Field(
        default_factory=list, max_length=MAX_GUIDE_ITEMS
    )


class GeneratedTopicTerm(BaseModel):
    model_config = ConfigDict(extra="ignore")

    term: str = Field(min_length=1, max_length=200)
    definition: str = Field(min_length=1)
    citations: CitationKeys = []


class GeneratedTopicPitfall(BaseModel):
    model_config = ConfigDict(extra="ignore")

    mistake: str = Field(min_length=1)
    correction: str = Field(min_length=1)
    citations: CitationKeys = []


class GeneratedExamTopicGuide(BaseModel):
    """A study guide for one planned topic, as the provider reported it.

    Deliberately not the course-wide ``GeneratedStudyGuideResponse``. That one
    carries an ``estimated_study_time`` and a whole-course difficulty, which
    would be a guess about a topic the plan already placed in a band, and it
    has no field for the thing a topic guide exists to give: what a student
    should be able to do with this topic once they have studied it.
    """

    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=1, max_length=200)
    overview: MaybeGeneratedCitedText
    sections: list[GeneratedTopicSection] = Field(
        min_length=1, max_length=MAX_GUIDE_SECTIONS
    )
    key_terms: list[GeneratedTopicTerm] = Field(
        default_factory=list, max_length=MAX_GUIDE_ITEMS
    )
    common_pitfalls: list[GeneratedTopicPitfall] = Field(
        default_factory=list, max_length=MAX_GUIDE_ITEMS
    )
    what_to_be_able_to_do: list[MaybeGeneratedCitedText] = Field(
        default_factory=list, max_length=MAX_GUIDE_ITEMS
    )
    coverage: Coverage
    confidence_notes: str = ""


class GeneratedExamTopicSummary(BaseModel):
    """The short-form sibling: what this topic is, in a form worth rereading."""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=1, max_length=200)
    summary: MaybeGeneratedCitedText
    key_points: list[MaybeGeneratedCitedText] = Field(
        min_length=1, max_length=MAX_SUMMARY_POINTS
    )
    coverage: Coverage
    confidence_notes: str = ""


class ExamTopicSection(BaseModel):
    heading: str
    body: MaybeCitedText
    key_points: list[MaybeCitedText] = []


class ExamTopicTerm(BaseModel):
    term: str
    definition: str
    citations: list[Citation] = []


class ExamTopicPitfall(BaseModel):
    mistake: str
    correction: str
    citations: list[Citation] = []


class ExamTopicGuideDocument(BaseModel):
    """The ``generated_outputs.content`` payload of one per-topic guide."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    version: Literal[1] = 1
    output_type: Literal["exam_topic_guide"] = "exam_topic_guide"
    topic_key: str
    display_label: str
    plan_output_id: int
    rank: int = 0
    priority_band: str = ""
    title: str
    overview: MaybeCitedText
    sections: list[ExamTopicSection] = []
    key_terms: list[ExamTopicTerm] = []
    common_pitfalls: list[ExamTopicPitfall] = []
    what_to_be_able_to_do: list[MaybeCitedText] = []
    coverage: Coverage | None = None
    confidence_notes: str = ""


class ExamTopicSummaryDocument(BaseModel):
    """The ``generated_outputs.content`` payload of one per-topic summary."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    version: Literal[1] = 1
    output_type: Literal["exam_topic_summary"] = "exam_topic_summary"
    topic_key: str
    display_label: str
    plan_output_id: int
    rank: int = 0
    priority_band: str = ""
    title: str
    summary: MaybeCitedText
    key_points: list[MaybeCitedText] = []
    coverage: Coverage | None = None
    confidence_notes: str = ""


class ExamArtifactGenerationSettings(BaseModel):
    """What one per-topic artifact was generated from.

    ``topic_key`` is here rather than in a column because it belongs to this
    contract: ``generated_outputs`` stays a table of generations rather than a
    table of Exam Mode, and the reopen path matches on this field.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    version: Literal[1] = 1
    output_type: str
    topic_key: str
    display_label: str
    plan_output_id: int
    analysis_output_id: int
    document_ids_requested: list[UUID] = []
    retrieval_limit: int
    retrieval_min_similarity: float
    material_max_characters: int
    topic_key_version: int
    prompt_template: str
    prompt_version: str


class ExamQuizGenerationSettings(ExamArtifactGenerationSettings):
    """What one quiz-backed per-topic artifact was generated from.

    ``answers_hidden`` is recorded rather than derived, because it is a
    property of the quiz a student sat: a topic exam served with its answers
    showing would not be the same assessment, and a reader of the history has
    to be able to tell which one it was.
    """

    question_count: int
    question_types: list[str] = []
    answers_hidden: bool = False


class ExamArtifactGenerationContext(RetrievalGenerationContext):
    """What retrieval actually produced for one per-topic artifact."""

    plan_output_id: int = 0
    topic_key: str = ""


class ExamTopicArtifactRequest(BaseModel):
    plan_output_id: int | None = Field(
        default=None,
        ge=1,
        description="The plan to study from, or omit to use the current one",
    )
    model: str | None = Field(
        default=None,
        description="Explicit model override, or omit to use the preferred model",
    )


class ExamTopicQuizRequest(ExamTopicArtifactRequest):
    question_count: int | None = Field(
        default=None,
        ge=1,
        le=20,
        description="How many questions to write, or omit to use the default",
    )


class ExamTopicQuizResult(RetrievedContext):
    quiz: QuizView
    generated_output_id: int
    created_at: datetime
    model_used: str | None = None
    credits_charged: float = 0.0
    answers_hidden: bool = False


class ExamTopicGuideResult(RetrievedContext):
    guide: ExamTopicGuideDocument
    generated_output_id: int
    created_at: datetime
    model_used: str | None = None
    credits_charged: float = 0.0


class ExamTopicSummaryResult(RetrievedContext):
    summary: ExamTopicSummaryDocument
    generated_output_id: int
    created_at: datetime
    model_used: str | None = None
    credits_charged: float = 0.0


# --------------------------------------------------------------- similar questions


class SimilarQuestionDifficultyPolicy(str, Enum):
    """How hard the new questions should be relative to the ones they mirror."""

    MATCH_SOURCE = "match_source"
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class SimilarQuestionRequest(ExamTopicArtifactRequest):
    """What a student may ask for when requesting similar questions.

    Deliberately narrow. The topic is the canonical key in the route, and the
    originals are named by the identifiers of rows this course already owns --
    never by text, never by a document, and never by a course the caller does
    not hold. Anything wider would let a request describe its own grounding,
    which is the one thing the server has to decide for itself.
    """

    source_question_ids: list[int] | None = Field(
        default=None,
        max_length=MAX_SIMILAR_QUESTIONS,
        description=(
            "Which of this topic's extracted past questions to mirror, or omit "
            "to use the ones the plan already found relevant"
        ),
    )
    question_count: int = Field(
        default=5,
        ge=MIN_QUIZ_QUESTIONS,
        le=MAX_QUIZ_QUESTIONS,
        description="How many questions to write",
    )
    difficulty_policy: SimilarQuestionDifficultyPolicy = Field(
        default=SimilarQuestionDifficultyPolicy.MATCH_SOURCE,
        description="Match each source's difficulty, or pin every question to one level",
    )
    requested_question_types: list[QuizQuestionType] | None = Field(
        default=None,
        min_length=1,
        max_length=4,
        description="Restrict the set to these question types, or omit to allow all",
    )
    request_id: UUID | None = Field(
        default=None,
        description=(
            "A client-generated identifier that makes a retry return the first "
            "result instead of generating and charging a second time"
        ),
    )

    @field_validator("source_question_ids")
    @classmethod
    def _reject_duplicate_sources(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        if not value:
            raise ValueError("source_question_ids must not be empty when supplied")
        if len(set(value)) != len(value):
            raise ValueError("source_question_ids must not repeat an identifier")
        if any(identifier < 1 for identifier in value):
            raise ValueError("source_question_ids must be positive identifiers")
        return value

    @field_validator("requested_question_types")
    @classmethod
    def _reject_duplicate_types(
        cls, value: list[QuizQuestionType] | None
    ) -> list[QuizQuestionType] | None:
        if value is None:
            return None
        if len(set(value)) != len(value):
            raise ValueError("requested_question_types must not repeat a type")
        return value


class GeneratedSimilarQuestion(BaseModel):
    """One fresh question written in the mould of an original.

    ``source_number`` is the position the prompt printed against the original,
    not a database identifier. A model is never shown a row id and never asked
    to echo one back; the application resolves the number against the questions
    it actually supplied, exactly as it resolves a citation key.

    The question itself is the ordinary quiz contract, which is what lets the
    result be stored, attempted, and graded by the machinery that already
    exists rather than by a second copy of it.
    """

    model_config = ConfigDict(extra="ignore")

    source_number: int = Field(ge=1)
    question: GeneratedQuizQuestion


class GeneratedSimilarQuestionResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=1, max_length=MAX_TITLE_CHARS)
    questions: list[GeneratedSimilarQuestion] = Field(
        default_factory=list, max_length=MAX_SIMILAR_QUESTIONS
    )


class ExamSimilarQuestionsSettings(ExamQuizGenerationSettings):
    """What one similar-question set was generated from.

    The source identifiers are recorded because the set's whole claim is that
    it mirrors questions this course actually set. Without them a reader cannot
    check that claim, and a deleted paper would leave the quiz asserting a
    provenance nothing can confirm.
    """

    source_question_ids: list[int] = []
    difficulty_policy: str = SimilarQuestionDifficultyPolicy.MATCH_SOURCE.value


class ExamSimilarQuestionsResult(RetrievedContext):
    quiz: QuizView
    generated_output_id: int
    created_at: datetime
    model_used: str | None = None
    credits_charged: float = 0.0
    answers_hidden: bool = True
    source_question_ids: list[int] = []


# --------------------------------------------------------------- course-level


class GeneratedReviewTopic(BaseModel):
    model_config = ConfigDict(extra="ignore")

    topic_label: str = Field(min_length=1, max_length=200)
    must_remember: list[MaybeGeneratedCitedText] = Field(
        default_factory=list, max_length=MAX_REVIEW_ITEMS
    )
    traps: list[MaybeGeneratedCitedText] = Field(
        default_factory=list, max_length=MAX_REVIEW_ITEMS
    )


class GeneratedExamReviewSheet(BaseModel):
    """The last-minute sheet, as the provider reported it."""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=1, max_length=200)
    topics: list[GeneratedReviewTopic] = Field(
        min_length=1, max_length=MAX_REVIEW_TOPICS
    )
    final_checks: list[MaybeGeneratedCitedText] = Field(
        default_factory=list, max_length=MAX_REVIEW_ITEMS
    )
    confidence_notes: str = ""


class ExamReviewTopic(BaseModel):
    topic_key: str = ""
    topic_label: str
    must_remember: list[MaybeCitedText] = []
    traps: list[MaybeCitedText] = []


class ExamReviewSheetDocument(BaseModel):
    """The ``generated_outputs.content`` payload of one review sheet."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    version: Literal[1] = 1
    output_type: Literal["exam_review_sheet"] = "exam_review_sheet"
    plan_output_id: int
    exam_date: date | None = None
    days_until_exam: int | None = None
    title: str
    topics: list[ExamReviewTopic] = []
    final_checks: list[MaybeCitedText] = []
    confidence_notes: str = ""


class ExamPlanArtifactRequest(BaseModel):
    plan_output_id: int | None = Field(
        default=None,
        ge=1,
        description="The plan to work from, or omit to use the current one",
    )
    model: str | None = Field(
        default=None,
        description="Explicit model override, or omit to use the preferred model",
    )


class MockExamQuestionMixEntry(BaseModel):
    """Exactly how many questions of one type the paper must hold."""

    model_config = ConfigDict(extra="forbid")

    question_type: QuizQuestionType
    count: int = Field(ge=1, le=MAX_QUIZ_QUESTIONS)


class ExamMockExamRequest(ExamPlanArtifactRequest):
    """What a student may configure about a mock examination.

    Duration is in minutes on the wire and seconds in the database: minutes are
    what a student sets and seconds are what a clock compares, and converting
    once at the boundary keeps the two from disagreeing.
    """

    question_count: int | None = Field(
        default=None,
        ge=MIN_QUIZ_QUESTIONS,
        le=MAX_QUIZ_QUESTIONS,
        description="How many questions the paper should hold, or omit for the default",
    )
    duration_minutes: int = Field(
        default=DEFAULT_MOCK_EXAM_MINUTES,
        ge=MIN_MOCK_EXAM_MINUTES,
        le=MAX_MOCK_EXAM_MINUTES,
        description="How long a sitting of this paper may last",
    )
    question_mix: list[MockExamQuestionMixEntry] | None = Field(
        default=None,
        min_length=1,
        max_length=4,
        description=(
            "Exactly how many questions of each type, or omit for the default shape"
        ),
    )
    topic_keys: list[str] | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_SELECTED_TOPICS,
        description=(
            "Which of the plan's topics the paper should cover, or omit for all of them"
        ),
    )
    request_id: UUID | None = Field(
        default=None,
        description=(
            "A client-generated identifier that makes a retry return the first "
            "result instead of generating and charging a second time"
        ),
    )

    @field_validator("question_mix")
    @classmethod
    def _reject_repeated_type(
        cls, value: list["MockExamQuestionMixEntry"] | None
    ) -> list["MockExamQuestionMixEntry"] | None:
        if value is None:
            return None
        seen = [entry.question_type for entry in value]
        if len(set(seen)) != len(seen):
            raise ValueError("question_mix must not name a question type twice")
        return value

    @field_validator("topic_keys")
    @classmethod
    def _reject_repeated_topic(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = [key.strip() for key in value]
        if any(not key for key in cleaned):
            raise ValueError("topic_keys must not contain a blank key")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("topic_keys must not repeat a key")
        return cleaned

    @model_validator(mode="after")
    def _mix_matches_the_paper_length(self) -> "ExamMockExamRequest":
        if self.question_mix is None or self.question_count is None:
            return self
        total = sum(entry.count for entry in self.question_mix)
        if total != self.question_count:
            raise ValueError(
                "question_mix must sum to question_count when both are supplied"
            )
        return self


class ExamCourseArtifactSettings(BaseModel):
    """What one course-level Exam Mode artifact was generated from."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    version: Literal[1] = 1
    output_type: str
    plan_output_id: int
    analysis_output_id: int
    topic_keys: list[str] = []
    document_ids_requested: list[UUID] = []
    retrieval_limit: int
    retrieval_min_similarity: float
    material_max_characters: int
    topic_key_version: int
    prompt_template: str
    prompt_version: str
    question_count: int | None = None
    answers_hidden: bool = False
    # The split the application calculated, recorded so a reader can check the
    # paper against what was actually asked for rather than taking it on trust.
    duration_minutes: int | None = None
    topic_quotas: list[dict] = []
    question_type_quotas: list[dict] = []
    allocation_policy_version: int | None = None


class ExamCourseArtifactContext(RetrievalGenerationContext):
    plan_output_id: int = 0
    topic_count: int = 0


class ExamMockExamResult(RetrievedContext):
    quiz: QuizView
    generated_output_id: int
    created_at: datetime
    model_used: str | None = None
    credits_charged: float = 0.0
    answers_hidden: bool = True
    duration_minutes: int = 0
    time_limit_seconds: int = 0


class ExamReviewSheetResult(RetrievedContext):
    review_sheet: ExamReviewSheetDocument
    generated_output_id: int
    created_at: datetime
    model_used: str | None = None
    credits_charged: float = 0.0
