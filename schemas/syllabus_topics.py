from pydantic import BaseModel, ConfigDict, Field

from schemas.document import SYLLABUS_MAX_CHARACTERS

MAX_GENERATED_SYLLABUS_TOPICS = 120


class SyllabusTopicsRequest(BaseModel):
    text: str = Field(min_length=1, max_length=SYLLABUS_MAX_CHARACTERS)


class SuggestedTopic(BaseModel):
    name: str
    weight_percent: int | None = None


class SyllabusTopicsResponse(BaseModel):
    topics: list[SuggestedTopic]


class GeneratedSyllabusTopic(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=200)
    weight_percent: float | None = Field(default=None, ge=0, le=100)


class GeneratedSyllabusTopicsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    topics: list[GeneratedSyllabusTopic] = Field(
        default_factory=list, max_length=MAX_GENERATED_SYLLABUS_TOPICS
    )


__all__ = [
    "MAX_GENERATED_SYLLABUS_TOPICS",
    "GeneratedSyllabusTopic",
    "GeneratedSyllabusTopicsResponse",
    "SuggestedTopic",
    "SyllabusTopicsRequest",
    "SyllabusTopicsResponse",
]
