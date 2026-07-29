from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class CourseBase(BaseModel):
    title: str
    description: Optional[str] = None
    instructor: str
    price: float = 0.0


class CourseCreate(CourseBase):
    pass


class CourseUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    instructor: Optional[str] = None
    price: Optional[float] = None
    is_deleted: Optional[bool] = None


class CourseResponse(CourseBase):
    id: int
    created_at: datetime
    is_deleted: bool = False

    class Config:
        from_attributes = True
