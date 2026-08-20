from datetime import datetime
from typing import Any

from pydantic import BaseModel


class GeneratedOutputSummary(BaseModel):
    """One stored generation, without its content, for history listings."""

    id: int
    course_id: int
    output_type: str
    user_id: int | None = None
    model_used: str | None = None
    created_at: datetime
    generation_settings: dict[str, Any] | None = None
    generation_context: dict[str, Any] | None = None


class GeneratedOutputDetail(GeneratedOutputSummary):
    """One stored generation with the content that was persisted for it.

    ``content`` stays loosely typed on purpose: a single row whose stored JSON no
    longer matches its feature schema must render rather than fail the read.
    """

    content: dict[str, Any] | str
