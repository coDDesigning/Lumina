from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models import Conversation, Course
from schemas.ai_usage import ErrorCategory, GenerationType
from schemas.conversation import ConversationType
from schemas.course_qa import CourseQAResponse
from services.ai_usage_logger import AiUsageLogger
from services.conversation import CONVERSATION_NOT_FOUND, ConversationService
from services.course_material import count_available_chunks
from services.prompt_loader import PromptLoader
from services.retrieval_material import (
    MaterialNotIndexedError,
    MaterialRetrievalError,
    NoRelevantMaterialError,
    RetrievedCourseMaterial,
    load_retrieved_material,
)
from services.retrieval_query import build_retrieval_query as make_retrieval_query
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
from utils.exceptions import NotFoundException


class CourseQAError(RuntimeError):
    """Course Q&A answer generation failed."""


class NoReadyCourseMaterialError(CourseQAError, CourseMaterialUnavailableError):
    """No processed course material is available for Course Q&A."""


@dataclass(frozen=True)
class CourseQAGeneration:
    response: CourseQAResponse
    material: RetrievedCourseMaterial
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
        *,
        query: str,
    ) -> RetrievedCourseMaterial:
        return load_retrieved_material(
            db,
            course_id,
            query=query,
            limit=settings.retrieval_chunk_limit,
            min_similarity=settings.retrieval_min_similarity,
            max_characters=settings.course_qa_material_max_chars,
        )

    @staticmethod
    def build_retrieval_query(course: Course | None, question: str) -> str:
        return make_retrieval_query(course, question)

    @classmethod
    def build_prompt(
        cls,
        course_material: str,
        question: str,
        conversation_history: str = "",
        learner_context: Any = None,
    ) -> str:
        return PromptLoader.render(
            cls.PROMPT_TEMPLATE_NAME,
            {
                "COURSE_MATERIAL": course_material,
                "CONVERSATION_HISTORY": conversation_history,
                "QUESTION": question,
            },
            learner_context=learner_context,
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
        course = db.get(Course, course_id)
        resolved_user_id = user_id
        if resolved_user_id is None and course is not None:
            resolved_user_id = course.owner_id

        def log_failure(category: ErrorCategory) -> None:
            if resolved_user_id:
                AiUsageLogger.log_failure(
                    db,
                    user_id=resolved_user_id,
                    course_id=course_id,
                    generation_type=GenerationType.COURSE_QA,
                    error_category=category,
                )
                try:
                    db.commit()
                except Exception:
                    db.rollback()

        conversation: Conversation | None = None
        if conversation_id is not None:
            if resolved_user_id is None:
                raise NotFoundException(CONVERSATION_NOT_FOUND)
            conversation = ConversationService.get_for_append(
                db,
                conversation_id,
                user_id=resolved_user_id,
                course_id=course_id,
                conversation_type=ConversationType.COURSE_QA,
            )

        if count_available_chunks(db, course_id) == 0:
            log_failure(ErrorCategory.NO_READY_MATERIAL)
            raise NoReadyCourseMaterialError(NO_READY_MATERIAL_MESSAGE)

        query = cls.build_retrieval_query(course, question)
        try:
            material = cls.get_course_material(db, course_id, query=query)
        except MaterialNotIndexedError:
            log_failure(ErrorCategory.MATERIAL_NOT_INDEXED)
            raise
        except NoRelevantMaterialError:
            log_failure(ErrorCategory.NO_RELEVANT_MATERIAL)
            raise
        except MaterialRetrievalError:
            log_failure(ErrorCategory.RETRIEVAL_ERROR)
            raise

        conversation_history = ConversationService.format_history(conversation)

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
                log_failure(ErrorCategory.INSUFFICIENT_CREDITS)
                raise InsufficientCreditsError("Insufficient credits.")

        try:
            if hasattr(provider, "generate_text_with_metadata"):
                answer, metadata = provider.generate_text_with_metadata(prompt)
            else:
                answer = provider.generate_text(prompt)
        except TextGenerationError as exc:
            if resolved_user_id:
                CreditService.refund(db, receipt)
                log_failure(
                    getattr(exc, "error_category", ErrorCategory.PROVIDER_ERROR)
                )
            raise CourseQAError("Text generation provider failed.") from exc
        except Exception:
            if resolved_user_id:
                CreditService.refund(db, receipt)
            raise

        if resolved_user_id is None:
            raise CourseQAError("Unable to determine conversation owner.")

        try:
            conversation = ConversationService.record_exchange(
                db,
                conversation=conversation,
                user_id=resolved_user_id,
                course_id=course_id,
                conversation_type=ConversationType.COURSE_QA,
                question=question,
                answer=answer,
            )
            AiUsageLogger.log_success(
                db,
                user_id=resolved_user_id,
                course_id=course_id,
                generation_type=GenerationType.COURSE_QA,
                metadata=metadata,
            )
            db.commit()
        except Exception:
            db.rollback()
            CreditService.refund(db, receipt)
            log_failure(ErrorCategory.UNKNOWN_ERROR)
            raise

        return CourseQAGeneration(
            response=CourseQAResponse(answer=answer),
            material=material,
            model_used=model_identifier(metadata),
            conversation_id=conversation.id,
        )
