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


class ProcessingJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    attempt_count: int
    max_attempts: int
    available_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    last_error_code: str | None
    last_error_message: str | None


class DocumentStatusResponse(BaseModel):
    document: DocumentResponse
    processing_job: ProcessingJobResponse
