from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models import Course, GeneratedOutput
from schemas.ai_usage import ErrorCategory, GenerationType
from schemas.study_guide import StudyGuideResponse, SummaryFormat
from services.ai_usage_logger import AiUsageLogger
from services.course_material import CourseMaterial, load_course_material
from services.prompt_loader import PromptLoader
from services.text_generation import (
    TextGenerationError,
    TextGenerationProvider,
    model_identifier,
)
from services.user import UserService
from utils.ai_errors import (
    NO_READY_MATERIAL_MESSAGE,
    CourseMaterialUnavailableError,
    InsufficientCreditsError,
    InvalidGeneratedStructureError,
)


SUMMARY_FORMAT_DIRECTIVES: dict[SummaryFormat, str] = {
    SummaryFormat.OVERVIEW: (
        "Write for a quick revision pass. Keep the summary at the shorter end of the "
        "allowed range and favour breadth over depth in every section."
    ),
    SummaryFormat.COMPREHENSIVE: (
        "Write a thorough study guide. Use the full allowed length of the summary and "
        "provide the maximum useful number of important terms, common mistakes, and "
        "learning objectives."
    ),
    SummaryFormat.KEY_CONCEPTS: (
        "Focus on definitions and core principles. Put the most effort into the key "
        "points and important terms sections, and keep the summary at the shorter end "
        "of the allowed range."
    ),
    SummaryFormat.EXAM_TIPS: (
        "Focus on exam preparation. Put the most effort into the exam tips and common "
        "mistakes sections, and keep the summary at the shorter end of the allowed range."
    ),
}


class StudyGuideGenerationError(RuntimeError):
    """Study guide generation failed."""


class NoReadyCourseMaterialError(
    StudyGuideGenerationError, CourseMaterialUnavailableError
):
    """No processed course material is available for study guide generation."""


class InvalidStudyGuideStructureError(
    StudyGuideGenerationError, InvalidGeneratedStructureError
):
    """The provider returned something that is not a valid study guide."""


@dataclass(frozen=True)
class StudyGuideGeneration:
    study_guide: StudyGuideResponse
    material: CourseMaterial
    model_used: str


class StudyGuideService:
    PROMPT_TEMPLATE_NAME = "study_guide"
    PROMPT_PATH = (
        Path(__file__).resolve().parents[1] / "app" / "prompts" / "study_guide.json"
    )

    @staticmethod
    def get_course_material(db: Session, course_id: int) -> CourseMaterial:
        return load_course_material(
            db,
            course_id,
            max_characters=settings.study_guide_material_max_chars,
        )

    @classmethod
    def build_prompt(
        cls,
        course_material: str,
        summary_format: SummaryFormat,
        topic_focus: str,
    ) -> str:
        directive = SUMMARY_FORMAT_DIRECTIVES[summary_format]
        return PromptLoader.render(
            cls.PROMPT_TEMPLATE_NAME,
            {
                "TEXT": course_material,
                "SUMMARY_FORMAT": (
                    f"Requested summary format: {summary_format.value}. {directive}"
                ),
                "TOPIC_FOCUS": topic_focus,
            },
        )

    @classmethod
    def generate(
        cls,
        db: Session,
        course_id: int,
        summary_format: SummaryFormat,
        topic_focus: str,
        provider: TextGenerationProvider,
        user_id: int | None = None,
    ) -> StudyGuideGeneration:
        resolved_user_id = user_id
        if resolved_user_id is None:
            course = db.get(Course, course_id)
            if course is not None:
                resolved_user_id = course.owner_id

        material = cls.get_course_material(db, course_id)

        if material.is_empty:
            if resolved_user_id:
                AiUsageLogger.log_failure(
                    db,
                    user_id=resolved_user_id,
                    course_id=course_id,
                    generation_type=GenerationType.STUDY_GUIDE,
                    error_category=ErrorCategory.NO_READY_MATERIAL,
                )
            raise NoReadyCourseMaterialError(NO_READY_MATERIAL_MESSAGE)

        prompt = cls.build_prompt(material.text, summary_format, topic_focus)
        metadata = None

        if resolved_user_id:
            charged = UserService.charge_credits(db, resolved_user_id, 1.0)
            if not charged:
                AiUsageLogger.log_failure(
                    db,
                    user_id=resolved_user_id,
                    course_id=course_id,
                    generation_type=GenerationType.STUDY_GUIDE,
                    error_category=ErrorCategory.INSUFFICIENT_CREDITS,
                )
                raise InsufficientCreditsError("Insufficient credits.")

        try:
            if hasattr(provider, "generate_json_with_metadata"):
                result, metadata = provider.generate_json_with_metadata(prompt)
            else:
                result = provider.generate_json(prompt)
        except TextGenerationError as exc:
            if resolved_user_id:
                UserService.refund_credits(db, resolved_user_id, 1.0)
                AiUsageLogger.log_failure(
                    db,
                    user_id=resolved_user_id,
                    course_id=course_id,
                    generation_type=GenerationType.STUDY_GUIDE,
                    error_category=getattr(
                        exc, "error_category", ErrorCategory.PROVIDER_ERROR
                    ),
                )
            raise StudyGuideGenerationError("Text generation provider failed.") from exc
        except Exception:
            if resolved_user_id:
                UserService.refund_credits(db, resolved_user_id, 1.0)
            raise

        try:
            validated = StudyGuideResponse.model_validate(result)
        except ValidationError as exc:
            if resolved_user_id:
                UserService.refund_credits(db, resolved_user_id, 1.0)
                AiUsageLogger.log_failure(
                    db,
                    user_id=resolved_user_id,
                    course_id=course_id,
                    generation_type=GenerationType.STUDY_GUIDE,
                    error_category=ErrorCategory.INVALID_STRUCTURE,
                    latency_ms=metadata.latency_ms if metadata else None,
                )
            raise InvalidStudyGuideStructureError(
                "Generated study guide has an invalid structure."
            ) from exc

        if resolved_user_id:
            AiUsageLogger.log_success(
                db,
                user_id=resolved_user_id,
                course_id=course_id,
                generation_type=GenerationType.STUDY_GUIDE,
                metadata=metadata,
            )

        return StudyGuideGeneration(
            study_guide=validated,
            material=material,
            model_used=model_identifier(metadata),
        )

    @staticmethod
    def save_generated_output(
        db: Session,
        course_id: int,
        study_guide: StudyGuideResponse,
        *,
        user_id: int,
        model_used: str,
    ) -> GeneratedOutput:
        generated_output = GeneratedOutput(
            course_id=course_id,
            user_id=user_id,
            model_used=model_used,
            output_type="study_guide",
            content=study_guide.model_dump_json(),
        )
        db.add(generated_output)
        db.flush()
        db.refresh(generated_output)
        db.commit()

        return generated_output
