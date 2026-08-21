# schemas/ocr_cleanup.py
"""Schemas for AI-assisted OCR text cleanup and normalization."""

from pydantic import BaseModel, ConfigDict, Field


class OcrCorrectionItem(BaseModel):
    """Details of a specific OCR correction made during cleanup."""

    model_config = ConfigDict(extra="forbid")

    original: str = Field(description="Corrupted or misread OCR fragment")
    corrected: str = Field(description="Repaired text")
    reason: str = Field(description="Rationale for correction")


class OcrCleanupResponse(BaseModel):
    """Structured response for OCR cleanup and normalization."""

    model_config = ConfigDict(extra="forbid")

    cleaned_text: str = Field(
        description="Cleaned, normalized, and correctly formatted text content"
    )
    corrections_made: list[OcrCorrectionItem] = Field(
        default_factory=list,
        description="List of significant OCR artifact fixes applied",
    )
    confidence_notes: str = Field(
        default="",
        description="Notes regarding unreadable sections or ambiguous symbols",
    )
