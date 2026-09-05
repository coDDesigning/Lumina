from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from schemas.prompt_context import EducationLevel


MAX_TOPICS = 50
MAX_TOPIC_LENGTH = 100


def _reject_nul(value: str | None) -> str | None:
    if value is not None and "\x00" in value:
        raise ValueError("Text fields cannot contain NUL characters")
    return value


def _blank_to_none(value: object) -> object:
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _normalize_topics(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        _reject_nul(value)
        topic = value.strip()
        if not topic:
            continue
        if len(topic) > MAX_TOPIC_LENGTH:
            raise ValueError(f"A topic cannot exceed {MAX_TOPIC_LENGTH} characters")
        key = topic.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(topic)
    if len(normalized) > MAX_TOPICS:
        raise ValueError(f"A course cannot have more than {MAX_TOPICS} topics")
    return normalized


class CourseBase(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    subject_area: str | None = Field(default=None, max_length=100)
    education_level: EducationLevel = EducationLevel.UNSPECIFIED
    description: str | None = None
    semester: str | None = Field(default=None, max_length=100)
    exam_date: date | None = None
    syllabus: str | None = None
    topics: list[str] = Field(default_factory=list)
    is_archived: bool = False


class CourseCreate(CourseBase):
    model_config = ConfigDict(use_enum_values=True, validate_default=True)

    @field_validator(
        "title",
        "description",
        "semester",
        "syllabus",
        "subject_area",
    )
    @classmethod
    def reject_nul(cls, value: str | None) -> str | None:
        return _reject_nul(value)

    @field_validator("exam_date", mode="before")
    @classmethod
    def blank_exam_date_is_absent(cls, value: object) -> object:
        return _blank_to_none(value)

    @field_validator("topics")
    @classmethod
    def normalize_topics(cls, value: list[str] | None) -> list[str] | None:
        return _normalize_topics(value)


class CourseUpdate(BaseModel):
    model_config = ConfigDict(use_enum_values=True, validate_default=True)

    title: str | None = Field(default=None, min_length=1, max_length=200)
    subject_area: str | None = Field(default=None, max_length=100)
    education_level: EducationLevel | None = None
    description: str | None = None
    semester: str | None = Field(default=None, max_length=100)
    exam_date: date | None = None
    syllabus: str | None = None
    topics: list[str] | None = None
    is_archived: bool | None = None

    @field_validator(
        "title",
        "description",
        "semester",
        "syllabus",
        "subject_area",
    )
    @classmethod
    def reject_nul(cls, value: str | None) -> str | None:
        return _reject_nul(value)

    @field_validator("exam_date", mode="before")
    @classmethod
    def blank_exam_date_is_absent(cls, value: object) -> object:
        return _blank_to_none(value)

    @field_validator("topics")
    @classmethod
    def normalize_topics(cls, value: list[str] | None) -> list[str] | None:
        return _normalize_topics(value)

    @model_validator(mode="after")
    def reject_null_for_required_columns(self) -> "CourseUpdate":
        required_columns = {"title", "education_level", "is_archived"}
        explicitly_null = required_columns & self.model_fields_set
        if any(getattr(self, field) is None for field in explicitly_null):
            raise ValueError("Required course fields cannot be null")
        return self


class CourseResponse(CourseBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_id: int
    owner_name: str | None = None
    owner_email: str | None = None
    created_at: datetime
    updated_at: datetime
    is_deleted: bool = False
    is_archived: bool = False
