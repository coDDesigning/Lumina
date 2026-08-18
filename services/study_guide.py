from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models import Course, GeneratedOutput
from schemas.ai_usage import ErrorCategory, GenerationType
from schemas.study_guide import StudyGuideResponse
from services.ai_usage_logger import AiUsageLogger
from services.course_material import CourseMaterial, load_course_material
from services.prompt_loader import PromptLoader
from services.text_generation import (
    TextGenerationError,
    TextGenerationProvider,
    model_identifier,
)
from utils.ai_errors import (
    NO_READY_MATERIAL_MESSAGE,
    CourseMaterialUnavailableError,
    InvalidGeneratedStructureError,
)


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
    def build_prompt(cls, course_material: str) -> str:
        return PromptLoader.render(cls.PROMPT_TEMPLATE_NAME, {"TEXT": course_material})

    @classmethod
    def generate(
        cls,
        db: Session,
        course_id: int,
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

        prompt = cls.build_prompt(material.text)
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
                    generation_type=GenerationType.STUDY_GUIDE,
                    error_category=getattr(
                        exc, "error_category", ErrorCategory.PROVIDER_ERROR
                    ),
                )
            raise StudyGuideGenerationError("Text generation provider failed.") from exc

        try:
            validated = StudyGuideResponse.model_validate(result)
        except ValidationError as exc:
            if resolved_user_id:
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
