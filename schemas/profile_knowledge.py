from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProfileKnowledgeBase(BaseModel):
    """Base schema for student profile knowledge."""

    topic: str = Field(min_length=1, max_length=200)
    detail: str = Field(min_length=1)

    @field_validator("topic", "detail")
    @classmethod
    def strip_and_validate(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("Text fields cannot contain NUL characters")
        stripped = value.strip()
        if not stripped:
            raise ValueError("Field cannot be empty or whitespace only")
        return stripped


class ProfileKnowledgeCreate(ProfileKnowledgeBase):
    """Payload to create a new profile knowledge entry."""

    pass


class ProfileKnowledgeUpdate(BaseModel):
    """Payload to update an existing profile knowledge entry."""

    topic: str | None = Field(default=None, min_length=1, max_length=200)
    detail: str | None = Field(default=None, min_length=1)

    @field_validator("topic", "detail")
    @classmethod
    def strip_and_validate(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if "\x00" in value:
            raise ValueError("Text fields cannot contain NUL characters")
        stripped = value.strip()
        if not stripped:
            raise ValueError("Field cannot be empty or whitespace only")
        return stripped


class ProfileKnowledgeResponse(BaseModel):
    """Response representation of a student profile knowledge item."""

    id: int
    user_id: int
    topic: str
    detail: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProfileKnowledgeImport(BaseModel):
    """Payload to bulk-import multiple profile knowledge entries."""

    items: list[ProfileKnowledgeCreate] = Field(min_length=1, max_length=100)


class ProfileKnowledgeListResponse(BaseModel):
    """List of profile knowledge items with count."""

    items: list[ProfileKnowledgeResponse]
    total: int
