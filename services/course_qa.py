from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models import Conversation, ConversationMessage, Course
from schemas.ai_usage import ErrorCategory, GenerationType
from schemas.course_qa import CourseQAResponse
from services.ai_usage_logger import AiUsageLogger
from services.course_material import CourseMaterial, load_course_material
from services.prompt_loader import PromptLoader
from services.text_generation import (
    TextGenerationError,
    TextGenerationProvider,
    model_identifier,
)
from services.credits import CreditService
from utils.ai_errors import (
    NO_READY_MATERIAL_MESSAGE,
    CourseMaterialUnavailableError,
    InsufficientCreditsError,
)


class CourseQAError(RuntimeError):
    """Course Q&A answer generation failed."""


class NoReadyCourseMaterialError(CourseQAError, CourseMaterialUnavailableError):
    """No processed course material is available for Course Q&A."""


class ConversationNotFoundError(CourseQAError):
    """Conversation does not exist or does not belong to this user/course."""


@dataclass(frozen=True)
class CourseQAGeneration:
    response: CourseQAResponse
    material: CourseMaterial
    model_used: str
    conversation_id: int


class CourseQAService:
    PROMPT_TEMPLATE_NAME = "course_qa"
    PROMPT_PATH = (
        Path(__file__).resolve().parents[1] / "app" / "prompts" / "course_qa.json"
    )

    @staticmethod
    def get_course_material(
        db: Session,
        course_id: int,
    ) -> CourseMaterial:
        return load_course_material(
            db,
            course_id,
            max_characters=settings.course_qa_material_max_chars,
        )

    @staticmethod
    def get_conversation(
        db: Session,
        conversation_id: int,
        *,
        user_id: int,
        course_id: int,
    ) -> Conversation:
        conversation = db.get(Conversation, conversation_id)

        if (
            conversation is None
            or conversation.user_id != user_id
            or conversation.course_id != course_id
        ):
            raise ConversationNotFoundError("Conversation not found.")

        return conversation

    @staticmethod
    def format_conversation_history(
        conversation: Conversation | None,
    ) -> str:
        if conversation is None:
            return ""

        return "\n".join(
            f"{'User' if message.role == 'user' else 'Assistant'}: {message.content}"
            for message in conversation.messages
        )

    @classmethod
    def build_prompt(
        cls,
        course_material: str,
        question: str,
        conversation_history: str = "",
    ) -> str:
        return PromptLoader.render(
            cls.PROMPT_TEMPLATE_NAME,
            {
                "COURSE_MATERIAL": course_material,
                "CONVERSATION_HISTORY": conversation_history,
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
        conversation_id: int | None = None,
    ) -> CourseQAGeneration:
        resolved_user_id = user_id
        if resolved_user_id is None:
            course = db.get(Course, course_id)
            if course is not None:
                resolved_user_id = course.owner_id

        material = cls.get_course_material(
            db,
            course_id,
        )

        if material.is_empty:
            if resolved_user_id:
                AiUsageLogger.log_failure(
                    db,
                    user_id=resolved_user_id,
                    course_id=course_id,
                    generation_type=GenerationType.COURSE_QA,
                    error_category=ErrorCategory.NO_READY_MATERIAL,
                )
            raise NoReadyCourseMaterialError(NO_READY_MATERIAL_MESSAGE)

        conversation: Conversation | None = None

        if conversation_id is not None:
            if resolved_user_id is None:
                raise ConversationNotFoundError("Conversation not found.")

            conversation = cls.get_conversation(
                db,
                conversation_id,
                user_id=resolved_user_id,
                course_id=course_id,
            )

        conversation_history = cls.format_conversation_history(conversation)

        prompt = cls.build_prompt(
            material.text,
            question,
            conversation_history,
        )
        metadata = None

        receipt = None
        if resolved_user_id:
            receipt = CreditService.charge(
                db, resolved_user_id, 1.0, source_type="course_qa"
            )
            if receipt is None:
                AiUsageLogger.log_failure(
                    db,
                    user_id=resolved_user_id,
                    course_id=course_id,
                    generation_type=GenerationType.COURSE_QA,
                    error_category=ErrorCategory.INSUFFICIENT_CREDITS,
                )
                raise InsufficientCreditsError("Insufficient credits.")

        try:
            if hasattr(provider, "generate_text_with_metadata"):
                answer, metadata = provider.generate_text_with_metadata(prompt)
            else:
                answer = provider.generate_text(prompt)
        except TextGenerationError as exc:
            if resolved_user_id:
                CreditService.refund(db, receipt)
                AiUsageLogger.log_failure(
                    db,
                    user_id=resolved_user_id,
                    course_id=course_id,
                    generation_type=GenerationType.COURSE_QA,
                    error_category=getattr(
                        exc, "error_category", ErrorCategory.PROVIDER_ERROR
                    ),
                )
            raise CourseQAError("Text generation provider failed.") from exc
        except Exception:
            if resolved_user_id:
                CreditService.refund(db, receipt)
            raise

        if resolved_user_id is None:
            raise CourseQAError("Unable to determine conversation owner.")

        if conversation is None:
            conversation = Conversation(
                user_id=resolved_user_id,
                course_id=course_id,
            )
            db.add(conversation)
            db.flush()
        else:
            conversation.updated_at = datetime.now(timezone.utc)

        db.add_all(
            [
                ConversationMessage(
                    conversation=conversation,
                    role="user",
                    content=question,
                ),
                ConversationMessage(
                    conversation=conversation,
                    role="assistant",
                    content=answer,
                ),
            ]
        )

        db.commit()

        if resolved_user_id:
            AiUsageLogger.log_success(
                db,
                user_id=resolved_user_id,
                course_id=course_id,
                generation_type=GenerationType.COURSE_QA,
                metadata=metadata,
            )

        return CourseQAGeneration(
            response=CourseQAResponse(answer=answer),
            material=material,
            model_used=model_identifier(metadata),
            conversation_id=conversation.id,
        )
