# schemas/visual_content.py
"""Schemas for visual content understanding and diagram/table analysis."""

from pydantic import BaseModel, ConfigDict, Field


class VisualElementDescription(BaseModel):
    """Description of a specific component within a visual artifact."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(description="Name or label of the visual component")
    details: str = Field(description="Detailed explanation of the component")


class VisualContentDescriptionResponse(BaseModel):
    """Structured response for visual content / diagram / table understanding."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(description="Short descriptive title of the visual content")
    visual_type: str = Field(
        description="Type of visual (e.g. diagram, chart, table, figure, equation)"
    )
    summary: str = Field(
        description="Concise overview of what the visual content represents"
    )
    key_elements: list[VisualElementDescription] = Field(
        default_factory=list,
        description="Key labeled components, axes, nodes, or regions",
    )
    data_points: list[str] = Field(
        default_factory=list,
        description="Specific numbers, metrics, or relationships depicted",
    )
    takeaway: str = Field(
        description="Primary conceptual or educational takeaway for the learner"
    )
    limitations: str | None = Field(
        default=None,
        description="Any ambiguities, low-resolution parts, or incomplete information",
    )
