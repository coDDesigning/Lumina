from pydantic import AliasChoices, BaseModel, Field

from schemas.generation import RetrievedContext


class AiTutorRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    conversation_id: int | None = Field(default=None, gt=0)
    use_profile_knowledge: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "use_profile_knowledge", "include_profile_context"
        ),
        description="Whether to include student profile knowledge context (opt-in)",
    )
    model: str | None = None

    @property
    def include_profile_context(self) -> bool:
        return self.use_profile_knowledge


class AiTutorResponse(BaseModel):
    answer: str


class AiTutorGenerationResult(RetrievedContext):
    answer: str
    conversation_id: int
