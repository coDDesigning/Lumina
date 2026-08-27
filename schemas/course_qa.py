from pydantic import AliasChoices, BaseModel, Field

from schemas.citation import Citation
from schemas.generation import RetrievedContext


class CourseQARequest(BaseModel):
    question: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="The question about course materials",
    )
    conversation_id: int | None = Field(
        default=None,
        gt=0,
        description="Existing conversation to continue, or omit to start a new one",
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


class CourseQAResponse(BaseModel):
    answer: str
    citations: list[Citation] = []


class CourseQAGenerationResult(RetrievedContext):
    answer: str
    citations: list[Citation] = []
    conversation_id: int
