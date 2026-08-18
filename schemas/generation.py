from pydantic import BaseModel


class BoundedContext(BaseModel):
    context_truncated: bool
    chunks_used: int
    chunks_available: int
