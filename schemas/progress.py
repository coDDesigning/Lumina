from datetime import datetime

from pydantic import BaseModel


class CourseProgressSummary(BaseModel):
    course_id: int
    attempts_count: int = 0
    average_score: float | None = None
    completion: float | None = None
    last_activity: datetime | None = None
