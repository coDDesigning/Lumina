from pydantic import BaseModel


class PromptGenerationRequest(BaseModel):
    description: str
    model: str | None = None


class PromptGenerationResponse(BaseModel):
    generated_prompt: str
