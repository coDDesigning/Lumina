from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import DocumentChunk, Quiz, QuizQuestion, UploadedDocument
from schemas.quiz import QuizGenerationResponse
from services.text_generation import TextGenerationError, TextGenerationProvider


class QuizGenerationError(RuntimeError):
    """Quiz generation failed."""


class NoReadyCourseMaterialError(QuizGenerationError):
    """No processed course material is available for quiz generation."""


class QuizService:
    PROMPT_PATH = (
        Path(__file__).resolve().parents[1] / "app" / "prompts" / "quiz_prompt.txt"
    )

    @staticmethod
    def get_course_material(
        db: Session,
        course_id: int,
    ) -> str:
        chunks = db.scalars(
            select(DocumentChunk.text)
            .join(
                UploadedDocument,
                DocumentChunk.document_id == UploadedDocument.id,
            )
            .where(
                DocumentChunk.course_id == course_id,
                UploadedDocument.status == "ready",
            )
            .order_by(
                DocumentChunk.document_id,
                DocumentChunk.chunk_index,
            )
        ).all()

        return "\n\n".join(text.strip() for text in chunks if text.strip())

    @classmethod
    def build_prompt(
        cls,
        course_material: str,
    ) -> str:
        prompt_template = cls.PROMPT_PATH.read_text(
            encoding="utf-8",
        )
        return prompt_template.replace(
            "{{TEXT}}",
            course_material,
        )

    @classmethod
    def generate(
        cls,
        db: Session,
        course_id: int,
        provider: TextGenerationProvider,
    ) -> QuizGenerationResponse:
        course_material = cls.get_course_material(
            db,
            course_id,
        )

        if not course_material:
            raise NoReadyCourseMaterialError("No ready course material is available.")

        prompt = cls.build_prompt(course_material)

        try:
            result = provider.generate_json(prompt)
        except TextGenerationError as exc:
            raise QuizGenerationError("Text generation provider failed.") from exc

        try:
            return QuizGenerationResponse.model_validate(result)
        except ValidationError as exc:
            raise QuizGenerationError(
                "Generated quiz has an invalid structure."
            ) from exc

    @staticmethod
    def save_generated_quiz(
        db: Session,
        course_id: int,
        quiz_data: QuizGenerationResponse,
    ) -> Quiz:
        quiz = Quiz(
            course_id=course_id,
            title=quiz_data.title,
        )

        db.add(quiz)
        db.flush()

        for question_index, question in enumerate(quiz_data.questions):
            db.add(
                QuizQuestion(
                    quiz_id=quiz.id,
                    question_index=question_index,
                    question_text=question.question,
                    options=question.options,
                    correct_option_index=question.correct_option_index,
                )
            )

        db.commit()
        db.refresh(quiz)

        return quiz
