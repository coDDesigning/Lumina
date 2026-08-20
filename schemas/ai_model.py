from pydantic import BaseModel


class AiModelInfo(BaseModel):
    id: str
    provider: str
    model: str
    display_name: str
    is_default: bool
