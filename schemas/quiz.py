from pydantic import BaseModel, Field


class GeneratedQuizQuestion(BaseModel):
    question_number: int = Field(ge=1, le=10)
    topic: str
    question: str
    options: list[str] = Field(min_length=4, max_length=4)
    correct_option_index: int = Field(ge=0, le=3)
    explanation: str


class QuizGenerationResponse(BaseModel):
    title: str
    questions: list[GeneratedQuizQuestion] = Field(
        min_length=10,
        max_length=10,
    )
