from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from schemas.generation import BoundedContext


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


class FlashcardRequest(BaseModel):
    use_profile_knowledge: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "use_profile_knowledge", "include_profile_context"
        ),
        description="Whether to include student profile knowledge context (opt-in)",
    )
    model: str | None = Field(
        default=None,
        description="Explicit model override, or omit to use preferred/default model",
    )

    @property
    def include_profile_context(self) -> bool:
        return self.use_profile_knowledge


class FlashcardGenerationSettings(BaseModel):
    """The options a stored flashcard deck was generated with."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    version: Literal[1] = 1
    output_type: Literal["flashcards"] = "flashcards"
    use_profile_knowledge: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "use_profile_knowledge", "include_profile_context"
        ),
    )

    @property
    def include_profile_context(self) -> bool:
        return self.use_profile_knowledge

    @classmethod
    def from_request(
        cls,
        request: FlashcardRequest | None = None,
    ) -> "FlashcardGenerationSettings":
        return cls(
            use_profile_knowledge=(request.use_profile_knowledge if request else False),
        )


class FlashcardGenerationContext(BaseModel):
    """What material assembly actually produced for a stored flashcard deck."""

    model_config = ConfigDict(extra="ignore")

    version: Literal[1] = 1
    chunks_used: int
    chunks_available: int
    truncated: bool
    profile_knowledge_used: bool = False
    profile_knowledge_items_used: int = 0
    profile_knowledge_characters_used: int = 0
    profile_knowledge_truncated: bool = False

    @classmethod
    def from_material(
        cls,
        material,
        profile_knowledge=None,
    ) -> "FlashcardGenerationContext":
        profile_used = profile_knowledge is not None and not profile_knowledge.is_empty
        return cls(
            chunks_used=material.chunks_used,
            chunks_available=material.chunks_available,
            truncated=material.truncated,
            profile_knowledge_used=profile_used,
            profile_knowledge_items_used=(
                profile_knowledge.items_used if profile_knowledge else 0
            ),
            profile_knowledge_characters_used=(
                len(profile_knowledge.text) if profile_knowledge else 0
            ),
            profile_knowledge_truncated=(
                profile_knowledge.truncated if profile_knowledge else False
            ),
        )


class FlashcardGenerationResult(BoundedContext):
    flashcards: FlashcardGenerationResponse
    generated_output_id: int | None = None
