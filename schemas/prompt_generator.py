from pydantic import BaseModel


class PromptGenerationRequest(BaseModel):
    description: str


class PromptGenerationResponse(BaseModel):
    generated_prompt: str
