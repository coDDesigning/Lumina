from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class GenerationType(str, Enum):
    STUDY_GUIDE = "study_guide"
    QUIZ = "quiz"
    FLASHCARD = "flashcard"
    AI_TUTOR = "ai_tutor"
    PROMPT_GENERATOR = "prompt_generator"


class ErrorCategory(str, Enum):
    PROVIDER_ERROR = "provider_error"
    INVALID_STRUCTURE = "invalid_structure"
    NO_READY_MATERIAL = "no_ready_material"
    EMPTY_RESPONSE = "empty_response"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    AUTHENTICATION_ERROR = "authentication_error"
    UNKNOWN_ERROR = "unknown_error"


class TokenUsage(BaseModel):
    prompt_tokens: int | None = Field(default=None, description="Input tokens")
    completion_tokens: int | None = Field(default=None, description="Output tokens")
    total_tokens: int | None = Field(default=None, description="Total tokens")


class AiUsageRecordCreate(BaseModel):
    user_id: int
    course_id: int | None = None
    generation_type: str
    provider: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    latency_ms: int | None = None
    success: bool
    error_category: str | None = None


class AiUsageLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    course_id: int | None = None
    generation_type: str
    provider: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    latency_ms: int | None = None
    success: bool
    error_category: str | None = None
    created_at: datetime
