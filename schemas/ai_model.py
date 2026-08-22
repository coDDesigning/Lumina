from pydantic import BaseModel


class AiModelInfo(BaseModel):
    id: str
    provider: str
    model: str
    display_name: str
    is_default: bool
    cost_hint: str = ""
    capabilities: list[str] = []
    description: str = ""
    is_local: bool = False
    supports_json: bool = True
