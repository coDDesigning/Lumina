from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CourseBase(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    instructor: str = Field(min_length=1, max_length=200)
    price: float = Field(default=0.0, ge=0, allow_inf_nan=False)


class CourseCreate(CourseBase):
    pass


class CourseUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    instructor: str | None = Field(default=None, min_length=1, max_length=200)
    price: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    is_deleted: bool | None = None

    @model_validator(mode="after")
    def reject_null_for_required_columns(self) -> "CourseUpdate":
        required_columns = {"title", "instructor", "price", "is_deleted"}
        explicitly_null = required_columns & self.model_fields_set
        if any(getattr(self, field) is None for field in explicitly_null):
            raise ValueError("Required course fields cannot be null")
        return self


class CourseResponse(CourseBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    is_deleted: bool = False
