from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import Course, DocumentChunk, GeneratedOutput, UploadedDocument
from schemas.ai_usage import ErrorCategory, GenerationType
from schemas.flashcard import FlashcardGenerationResponse
from services.ai_usage_logger import AiUsageLogger
from services.text_generation import TextGenerationError, TextGenerationProvider


class FlashcardGenerationError(RuntimeError):
    """Flashcard generation failed."""


class NoReadyCourseMaterialError(FlashcardGenerationError):
    """No processed course material is available for flashcard generation."""


class FlashcardService:
    PROMPT_PATH = (
        Path(__file__).resolve().parents[1] / "app" / "prompts" / "flashcard_prompt.txt"
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
        user_id: int | None = None,
    ) -> FlashcardGenerationResponse:
        resolved_user_id = user_id
        if resolved_user_id is None:
            course = db.get(Course, course_id)
            if course is not None:
                resolved_user_id = course.owner_id

        course_material = cls.get_course_material(
            db,
            course_id,
        )

        if not course_material:
            if resolved_user_id:
                AiUsageLogger.log_failure(
                    db,
                    user_id=resolved_user_id,
                    course_id=course_id,
                    generation_type=GenerationType.FLASHCARD,
                    error_category=ErrorCategory.NO_READY_MATERIAL,
                )
            raise NoReadyCourseMaterialError("No ready course material is available.")

        prompt = cls.build_prompt(course_material)
        metadata = None

        try:
            if hasattr(provider, "generate_json_with_metadata"):
                result, metadata = provider.generate_json_with_metadata(prompt)
            else:
                result = provider.generate_json(prompt)
        except TextGenerationError as exc:
            if resolved_user_id:
                AiUsageLogger.log_failure(
                    db,
                    user_id=resolved_user_id,
                    course_id=course_id,
                    generation_type=GenerationType.FLASHCARD,
                    error_category=ErrorCategory.PROVIDER_ERROR,
                )
            raise FlashcardGenerationError("Text generation provider failed.") from exc

        try:
            validated = FlashcardGenerationResponse.model_validate(result)
        except ValidationError as exc:
            if resolved_user_id:
                AiUsageLogger.log_failure(
                    db,
                    user_id=resolved_user_id,
                    course_id=course_id,
                    generation_type=GenerationType.FLASHCARD,
                    error_category=ErrorCategory.INVALID_STRUCTURE,
                    latency_ms=metadata.latency_ms if metadata else None,
                )
            raise FlashcardGenerationError(
                "Generated flashcards have an invalid structure."
            ) from exc

        if resolved_user_id:
            AiUsageLogger.log_success(
                db,
                user_id=resolved_user_id,
                course_id=course_id,
                generation_type=GenerationType.FLASHCARD,
                metadata=metadata,
            )

        return validated

    @staticmethod
    def save_generated_flashcards(
        db: Session,
        course_id: int,
        flashcards: FlashcardGenerationResponse,
    ) -> GeneratedOutput:
        generated_output = GeneratedOutput(
            course_id=course_id,
            output_type="flashcards",
            content=flashcards.model_dump_json(),
        )

        db.add(generated_output)
        db.flush()
        db.refresh(generated_output)
        db.commit()

        return generated_output
