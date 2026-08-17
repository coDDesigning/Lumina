from typing import Literal

from pydantic import BaseModel, Field


class GeneratedFlashcard(BaseModel):
    card_number: int = Field(ge=1, le=10)
    difficulty: Literal["Easy", "Medium", "Hard"]
    front: str
    back: str


class FlashcardGenerationResponse(BaseModel):
    deck_title: str
    card_count: int = Field(ge=1, le=10)
    flashcards: list[GeneratedFlashcard] = Field(
        min_length=1,
        max_length=10,
    )
