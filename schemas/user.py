from pydantic import BaseModel
from typing import Optional
from enum import Enum

class Role(str, Enum):
    """Defines available user roles in the system."""
    ADMIN = "admin"
    USER = "user"

class UserBase(BaseModel):
    """Base schema containing common user fields."""
    name: str
    email: str

class UserCreate(UserBase):
    """Schema for user registration payload."""
    password: str

class UserResponse(UserBase):
    """Schema for returning user data securely (excludes password)."""
    id: int
    role: Role
    is_banned: bool
    credits: float
    preferred_model: str

    class Config:
        from_attributes = True

class UserUpdate(BaseModel):
    """Schema for updating user profile or admin actions."""
    role: Optional[Role] = None
    is_banned: Optional[bool] = None
    credits: Optional[float] = None
    preferred_model: Optional[str] = None
