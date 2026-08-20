from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from schemas.generation import BoundedContext


class SummaryFormat(str, Enum):
    OVERVIEW = "overview"
    COMPREHENSIVE = "comprehensive"
    KEY_CONCEPTS = "key_concepts"
    EXAM_TIPS = "exam_tips"


class StudyGuideRequest(BaseModel):
    summary_format: SummaryFormat
    topic_focus: str = Field(min_length=1, max_length=200)
    model: str | None = Field(
        default=None,
        description="Explicit model override, or omit to use preferred/default model",
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


class StudyGuideGenerationResult(BoundedContext):
    study_guide: StudyGuideResponse
