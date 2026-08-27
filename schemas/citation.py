from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict


class Citation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: int = 1
    key: str
    document_id: UUID
    document_label: str
    page_start: int | None = None
    page_end: int | None = None


def _as_cited(value: Any) -> Any:
    if isinstance(value, str):
        return {"text": value, "citations": []}
    return value


def _as_keys(value: Any) -> Any:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


CitationKeys = Annotated[list[str], BeforeValidator(_as_keys)]


class GeneratedCitedText(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str
    citations: CitationKeys = []


class CitedText(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str
    citations: list[Citation] = []


MaybeCitedText = Annotated[CitedText, BeforeValidator(_as_cited)]
MaybeGeneratedCitedText = Annotated[GeneratedCitedText, BeforeValidator(_as_cited)]
