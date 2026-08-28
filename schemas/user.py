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
        # Validated here rather than on the field so the policy can see the
        # name and address the password must not be built out of.
        validate_password(self.password, identifiers=(self.name, self.email))
        return self


class PasswordChangeRequest(BaseModel):
    """An authenticated password change: prove the old one, then set a new one.

    The current password is required even though the caller already holds a
    valid token, because a token that leaked is exactly the case this stops
    from becoming a permanent takeover.
    """

    current_password: str
    new_password: str


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


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=1, max_length=255)
