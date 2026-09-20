"""API schemas for registered documents."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from schemas.prompt_context import DocumentMaterialKind

SYLLABUS_MAX_CHARACTERS = 20_000


class VisualAnalysisSummary(BaseModel):
    total: int
    described: int
    pending: int
    failed: int
    failure_reason: str | None
    failed_page_numbers: list[int]
    crowded_pages: int
    crowded_page_numbers: list[int]
    stopped_error_code: str | None


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    original_file_name: str
    file_type: str
    mime_type: str
    material_kind: DocumentMaterialKind = DocumentMaterialKind.UNSPECIFIED
    file_size: int
    course_id: int
    status: str
    visual_analysis_status: str = "not_applicable"
    visual_analysis: VisualAnalysisSummary | None = None
    created_at: datetime
    updated_at: datetime


class DocumentUploadResponse(BaseModel):
    document: DocumentResponse
    duplicate: bool


class SyllabusExtractionResponse(BaseModel):
    """Plain text read out of a syllabus upload that was never persisted."""

    text: str
    truncated: bool


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
    processing_stage: str | None
    failed_stage: str | None


class DocumentStatusResponse(BaseModel):
    document: DocumentResponse
    processing_job: ProcessingJobResponse
