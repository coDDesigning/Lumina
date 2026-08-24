from datetime import datetime
from typing import Literal

from pydantic import BaseModel

ActivityKind = Literal["generation", "attempt"]

QUIZ_ATTEMPT_ACTION = "quiz_attempt"


class ActivityItem(BaseModel):
    kind: ActivityKind
    action_type: str
    course_id: int
    course_title: str
    occurred_at: datetime
    output_id: int | None = None
    quiz_id: int | None = None
    attempt_id: int | None = None
    topic: str | None = None
    score: float | None = None
