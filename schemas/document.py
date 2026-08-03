"""API schemas for registered documents."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    original_file_name: str
    file_type: str
    mime_type: str
    file_size: int
    course_id: int
    status: str
    created_at: datetime
    updated_at: datetime


class DocumentUploadResponse(BaseModel):
    document: DocumentResponse
    duplicate: bool
