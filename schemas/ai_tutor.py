from pydantic import BaseModel


class AiTutorRequest(BaseModel):
    question: str
    course_id: int


class AiTutorResponse(BaseModel):
    answer: str
