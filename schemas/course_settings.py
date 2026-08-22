from pydantic import BaseModel, Field


class CourseSettingsResponse(BaseModel):
    study_mode: str
    difficulty: str
    question_count: int = Field(ge=5, le=50)
    summary_length: str
    detail_level: str


class CourseSettingsUpdate(BaseModel):
    study_mode: str | None = None
    difficulty: str | None = None
    question_count: int | None = Field(default=None, ge=5, le=50)
    summary_length: str | None = None
    detail_level: str | None = None
