from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel


class GenerationType(str, Enum):
    STUDY_GUIDE = "study_guide"
    QUIZ = "quiz"
    FLASHCARD = "flashcard"
    AI_TUTOR = "ai_tutor"
    PROMPT_GENERATOR = "prompt_generator"
    COURSE_QA = "course_qa"
    QUIZ_GRADING = "quiz_grading"
    EXAM_TOPIC_ANALYSIS = "exam_topic_analysis"
    PAST_EXAM_EXTRACTION = "past_exam_extraction"
    EXAM_TOPIC_GUIDE = "exam_topic_guide"
    EXAM_TOPIC_SUMMARY = "exam_topic_summary"
    EXAM_TOPIC_PRACTICE = "exam_topic_practice"
    EXAM_TOPIC_EXAM = "exam_topic_exam"
    EXAM_SIMILAR_QUESTIONS = "exam_similar_questions"
    EXAM_MOCK_EXAM = "exam_mock_exam"
    EXAM_REVIEW_SHEET = "exam_review_sheet"


class ErrorCategory(str, Enum):
    PROVIDER_ERROR = "provider_error"
    INVALID_STRUCTURE = "invalid_structure"
    NO_READY_MATERIAL = "no_ready_material"
    NO_RELEVANT_MATERIAL = "no_relevant_material"
    MATERIAL_NOT_INDEXED = "material_not_indexed"
    RETRIEVAL_ERROR = "retrieval_error"
    EMPTY_RESPONSE = "empty_response"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    AUTHENTICATION_ERROR = "authentication_error"
    INSUFFICIENT_CREDITS = "insufficient_credits"
    UNKNOWN_ERROR = "unknown_error"


class AiCostTotals(BaseModel):
    successful_generations: int
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_usd: float
    unpriced_generations: int


class AiCostDailyRow(AiCostTotals):
    date: date
    provider: str
    model: str
    pricing_version: str | None = None


class AiCostReport(BaseModel):
    timezone: Literal["UTC"] = "UTC"
    start_date: date
    end_date: date
    totals: AiCostTotals
    daily: list[AiCostDailyRow]
