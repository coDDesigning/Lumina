from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import Course, DocumentChunk, UploadedDocument
from schemas.ai_tutor import AiTutorResponse
from schemas.ai_usage import ErrorCategory, GenerationType
from services.ai_usage_logger import AiUsageLogger
from services.prompt_loader import PromptLoader
from services.text_generation import TextGenerationError, TextGenerationProvider


class AiTutorError(RuntimeError):
    """AI tutor response generation failed."""


class NoReadyCourseMaterialError(AiTutorError):
    """No processed course material is available for AI tutor chat."""


class AiTutorService:
    PROMPT_TEMPLATE_NAME = "ai_tutor"
    PROMPT_PATH = (
        Path(__file__).resolve().parents[1] / "app" / "prompts" / "ai_tutor.json"
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
        question: str,
    ) -> str:
        return PromptLoader.render(
            cls.PROMPT_TEMPLATE_NAME,
            {
                "COURSE_MATERIAL": course_material,
                "QUESTION": question,
            },
        )

    @classmethod
    def generate(
        cls,
        db: Session,
        course_id: int,
        question: str,
        provider: TextGenerationProvider,
        user_id: int | None = None,
    ) -> AiTutorResponse:
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
                    generation_type=GenerationType.AI_TUTOR,
                    error_category=ErrorCategory.NO_READY_MATERIAL,
                )
            raise NoReadyCourseMaterialError("No ready course material is available.")

        prompt = cls.build_prompt(
            course_material,
            question,
        )
        metadata = None

        try:
            if hasattr(provider, "generate_text_with_metadata"):
                answer, metadata = provider.generate_text_with_metadata(prompt)
            else:
                answer = provider.generate_text(prompt)
        except TextGenerationError as exc:
            if resolved_user_id:
                AiUsageLogger.log_failure(
                    db,
                    user_id=resolved_user_id,
                    course_id=course_id,
                    generation_type=GenerationType.AI_TUTOR,
                    error_category=getattr(
                        exc, "error_category", ErrorCategory.PROVIDER_ERROR
                    ),
                )
            raise AiTutorError("Text generation provider failed.") from exc

        if resolved_user_id:
            AiUsageLogger.log_success(
                db,
                user_id=resolved_user_id,
                course_id=course_id,
                generation_type=GenerationType.AI_TUTOR,
                metadata=metadata,
            )

        return AiTutorResponse(answer=answer)
