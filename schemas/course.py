from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from schemas.prompt_context import EducationLevel


def _reject_nul(value: str | None) -> str | None:
    if value is not None and "\x00" in value:
        raise ValueError("Text fields cannot contain NUL characters")
    return value


class CourseBase(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    subject_area: str | None = Field(default=None, max_length=100)
    education_level: EducationLevel = EducationLevel.UNSPECIFIED
    description: str | None = None
    semester: str | None = Field(default=None, max_length=100)
    exam_date: str | None = Field(default=None, max_length=20)
    syllabus: str | None = None
    topics: str | None = None
    is_archived: bool = False


class CourseCreate(CourseBase):
    model_config = ConfigDict(use_enum_values=True, validate_default=True)

    @field_validator(
        "title",
        "description",
        "semester",
        "exam_date",
        "syllabus",
        "topics",
        "subject_area",
    )
    @classmethod
    def reject_nul(cls, value: str | None) -> str | None:
        return _reject_nul(value)


class CourseUpdate(BaseModel):
    model_config = ConfigDict(use_enum_values=True, validate_default=True)

    title: str | None = Field(default=None, min_length=1, max_length=200)
    subject_area: str | None = Field(default=None, max_length=100)
    education_level: EducationLevel | None = None
    description: str | None = None
    semester: str | None = Field(default=None, max_length=100)
    exam_date: str | None = Field(default=None, max_length=20)
    syllabus: str | None = None
    topics: str | None = None
    is_archived: bool | None = None

    @field_validator(
        "title",
        "description",
        "semester",
        "exam_date",
        "syllabus",
        "topics",
        "subject_area",
    )
    @classmethod
    def reject_nul(cls, value: str | None) -> str | None:
        return _reject_nul(value)

    @model_validator(mode="after")
    def reject_null_for_required_columns(self) -> "CourseUpdate":
        required_columns = {"title", "education_level"}
        explicitly_null = required_columns & self.model_fields_set
        if any(getattr(self, field) is None for field in explicitly_null):
            raise ValueError("Required course fields cannot be null")
        return self


class CourseResponse(CourseBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_id: int
    created_at: datetime
    updated_at: datetime
    is_deleted: bool = False
    is_archived: bool = False
