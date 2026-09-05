from typing import Literal

from pydantic import BaseModel, Field, model_validator

StudyMode = Literal["Exam", "General"]
Difficulty = Literal["Adaptive", "Easy", "Medium", "Hard"]
SummaryLength = Literal["Short", "Medium", "Long"]
DetailLevel = Literal["Concise", "Balanced", "Detailed"]


class CourseSettingsResponse(BaseModel):
    study_mode: str
    difficulty: str
    question_count: int = Field(ge=5, le=50)
    summary_length: str
    detail_level: str


class CourseSettingsUpdate(BaseModel):
    study_mode: StudyMode | None = None
    difficulty: Difficulty | None = None
    question_count: int | None = Field(default=None, ge=5, le=50)
    summary_length: SummaryLength | None = None
    detail_level: DetailLevel | None = None

    @model_validator(mode="after")
    def reject_null_for_required_columns(self) -> "CourseSettingsUpdate":
        required_columns = {
            "study_mode",
            "difficulty",
            "question_count",
            "summary_length",
            "detail_level",
        }
        explicitly_null = required_columns & self.model_fields_set
        if any(getattr(self, field) is None for field in explicitly_null):
            raise ValueError("Required course settings fields cannot be null")
        return self
