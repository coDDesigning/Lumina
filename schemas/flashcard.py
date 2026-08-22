from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from schemas.generation import RetrievalGenerationContext, RetrievedContext


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
    topic_focus: str = Field(
        default="All Topics",
        min_length=1,
        max_length=200,
        description="The topic focus, or omit to use 'All Topics'",
    )
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
    topic_focus: str = "All Topics"
    use_profile_knowledge: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "use_profile_knowledge", "include_profile_context"
        ),
    )
    retrieval_limit: int = 24
    retrieval_min_similarity: float = 0.25

    @property
    def include_profile_context(self) -> bool:
        return self.use_profile_knowledge

    @classmethod
    def from_request(
        cls,
        request: FlashcardRequest | None = None,
        *,
        retrieval_limit: int = 24,
        retrieval_min_similarity: float = 0.25,
    ) -> "FlashcardGenerationSettings":
        return cls(
            topic_focus=(
                request.topic_focus
                if request is not None and request.topic_focus is not None
                else "All Topics"
            ),
            use_profile_knowledge=(
                request.use_profile_knowledge if request is not None else False
            ),
            retrieval_limit=retrieval_limit,
            retrieval_min_similarity=retrieval_min_similarity,
        )


class FlashcardGenerationContext(RetrievalGenerationContext):
    """What retrieval actually produced for a stored flashcard deck."""


class FlashcardGenerationResult(RetrievedContext):
    flashcards: FlashcardGenerationResponse
    generated_output_id: int | None = None
