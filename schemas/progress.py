from datetime import datetime
from typing import Literal

from pydantic import BaseModel

CourseStatus = Literal[
    "no_documents",
    "processing",
    "ready",
    "practiced",
    "mastered",
]


class CourseProgressSummary(BaseModel):
    course_id: int
    status: CourseStatus
    attempts_count: int = 0
    average_score: float | None = None
    completion: float | None = None
    total_time_spent_seconds: int | None = None
    last_activity: datetime | None = None
