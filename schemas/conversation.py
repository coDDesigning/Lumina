from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel


class ConversationType(StrEnum):
    COURSE_QA = "course_qa"
    AI_TUTOR = "ai_tutor"


class ConversationMessageResponse(BaseModel):
    id: int
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime


class ConversationSummary(BaseModel):
    id: int
    course_id: int
    user_id: int
    conversation_type: ConversationType
    preview: str
    message_count: int
    created_at: datetime
    updated_at: datetime


class ConversationDetail(ConversationSummary):
    messages: list[ConversationMessageResponse]
