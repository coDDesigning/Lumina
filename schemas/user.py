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
from utils.password_policy import validate_password


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

    password: str

    @field_validator("name")
    @classmethod
    def reject_name_nul(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("Text fields cannot contain NUL characters")
        return value

    @model_validator(mode="after")
    def enforce_password_policy(self) -> "UserCreate":
        validate_password(self.password)
        return self


class PasswordChangeRequest(BaseModel):
    """An authenticated password change: prove the old one, then set a new one.

    The current password is required even though the caller already holds a
    valid token, because a token that leaked is exactly the case this stops
    from becoming a permanent takeover. The new password is bounded here only
    to reject empty and absurd input cheaply; ``UserService.change_password``
    applies the real policy through ``utils/password_policy.py``.
    """

    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=1, max_length=255)


class UserResponse(UserBase):
    """Schema for returning user data securely (excludes password)."""

    id: int
    role: Role
    is_banned: bool
    is_email_verified: bool
    credits: float | None
    preferred_model: str
    education_level: EducationLevel = EducationLevel.UNSPECIFIED

    model_config = ConfigDict(from_attributes=True)


class UserUpdate(BaseModel):
    """Schema for updating user profile or admin actions."""

    name: str | None = Field(None, max_length=255)
    role: Role | None = None
    is_banned: bool | None = None
    preferred_model: str | None = Field(None, min_length=1, max_length=100)
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


def mask_api_key(key: str | None) -> str | None:
    """Mask an API key for safe presentation to the frontend (e.g. sk-...****)."""
    if not key:
        return None
    clean = key.strip()
    if not clean:
        return None
    if len(clean) <= 8:
        return f"{clean[:2]}...****"
    return f"{clean[:6]}...****"


class UserApiKeysUpdateRequest(BaseModel):
    """Payload for saving, updating, or clearing BYOK API keys."""

    openai_api_key: str | None = Field(None, max_length=512)
    gemini_api_key: str | None = Field(None, max_length=512)
    anthropic_api_key: str | None = Field(None, max_length=512)


class UserApiKeysResponse(BaseModel):
    """Masked view of configured BYOK API keys."""

    openai_api_key: str | None = None
    gemini_api_key: str | None = None
    anthropic_api_key: str | None = None
    has_openai_key: bool = False
    has_gemini_key: bool = False
    has_anthropic_key: bool = False

