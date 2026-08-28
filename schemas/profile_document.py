"""API schemas for profile-knowledge documents."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ProfileDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    original_file_name: str
    file_type: str
    mime_type: str
    file_size: int
    user_id: int
    status: str
    processing_error: str | None = None
    created_at: datetime
    updated_at: datetime


class ProfileDocumentUploadResponse(BaseModel):
    document: ProfileDocumentResponse
    duplicate: bool


class ProfileProcessingJobResponse(BaseModel):
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
    processing_stage: str | None
    failed_stage: str | None


class ProfileDocumentStatusResponse(BaseModel):
    document: ProfileDocumentResponse
    processing_job: ProfileProcessingJobResponse | None
