from enum import Enum

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

from schemas.prompt_context import EducationLevel


class Role(str, Enum):
    """Defines available user roles in the system."""

    ADMIN = "admin"
    USER = "user"


class UserBase(BaseModel):
    """Base schema containing common user fields."""

    name: str = Field(min_length=1, max_length=255)
    email: EmailStr = Field(max_length=255)


class UserCreate(UserBase):
    """Schema for user registration payload."""

    password: str = Field(min_length=8)

    @field_validator("name")
    @classmethod
    def reject_name_nul(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("Text fields cannot contain NUL characters")
        return value

    @field_validator("password")
    @classmethod
    def validate_bcrypt_length(cls, password: str) -> str:
        if len(password.encode("utf-8")) > 72:
            raise ValueError("Password must be at most 72 UTF-8 bytes")
        return password


class UserResponse(UserBase):
    """Schema for returning user data securely (excludes password)."""

    id: int
    role: Role
    is_banned: bool
    credits: float | None
    preferred_model: str
    education_level: EducationLevel = EducationLevel.UNSPECIFIED

    model_config = ConfigDict(from_attributes=True)


class UserUpdate(BaseModel):
    """Schema for updating user profile or admin actions."""

    role: Role | None = None
    is_banned: bool | None = None
    preferred_model: str | None = Field(default=None, min_length=1, max_length=100)
    education_level: EducationLevel | None = None

    @field_validator("preferred_model")
    @classmethod
    def reject_model_nul(cls, value: str | None) -> str | None:
        if value is not None and "\x00" in value:
            raise ValueError("Text fields cannot contain NUL characters")
        return value

    @model_validator(mode="after")
    def reject_null_for_required_columns(self) -> "UserUpdate":
        required_columns = {
            "role",
            "is_banned",
            "preferred_model",
            "education_level",
        }
        explicitly_null = required_columns & self.model_fields_set
        if any(getattr(self, field) is None for field in explicitly_null):
            raise ValueError("Required user fields cannot be null")
        return self
