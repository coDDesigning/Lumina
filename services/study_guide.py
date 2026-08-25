from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models import Course, CourseSettings, GeneratedOutput
from schemas.ai_usage import ErrorCategory, GenerationType
from schemas.study_guide import (
    DetailLevel,
    StudyGuideRequest,
    StudyGuideResponse,
    SummaryFormat,
    SummaryLength,
    SummaryMode,
)
from services.ai_usage_logger import AiUsageLogger
from services.generated_output import GeneratedOutputService
from services.profile_knowledge import (
    ProfileKnowledgeContext,
    assemble_generation_context,
    format_profile_context,
)
from schemas.prompt_context import PromptContext
from services.document_lock import acquire_generation_locks
from services.prompt_context import resolve_prompt_context
from services.prompt_loader import PromptLoader
from services.course_material import count_available_chunks
from services.retrieval_query import build_retrieval_query
from services.retrieval_material import (
    MaterialNotIndexedError,
    MaterialRetrievalError,
    NoRelevantMaterialError,
    RetrievedCourseMaterial,
    load_retrieved_material,
)
from services.text_generation import (
    TextGenerationError,
    TextGenerationProvider,
    model_identifier,
)
from services.credits import ChargeReceipt, CreditService, GENERATION_CREDIT_COSTS
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

SUMMARY_LENGTH_DIRECTIVES: dict[SummaryLength, str] = {
    SummaryLength.SHORT: "Between 120 and 180 words.",
    SummaryLength.MEDIUM: "Between 200 and 300 words.",
    SummaryLength.LONG: "Between 400 and 600 words.",
}

DETAIL_LEVEL_DIRECTIVES: dict[DetailLevel, str] = {
    DetailLevel.BASIC: (
        "Requested detail level: basic. Explain concepts simply, stay with essential "
        "definitions, and leave out secondary caveats and edge cases."
    ),
    DetailLevel.STANDARD: (
        "Requested detail level: standard. Balance definitions with the reasoning "
        "behind them, and include an example where it aids understanding."
    ),
    DetailLevel.DETAILED: (
        "Requested detail level: detailed. Cover mechanisms, relationships between "
        "concepts, important caveats, and worked examples drawn from the course "
        "material."
    ),
}

SUMMARY_MODE_DIRECTIVES: dict[SummaryMode, str] = {
    SummaryMode.GENERAL: (
        "Requested summary mode: general. Teach and organize the important material as "
        "a coherent overview of the topic."
    ),
    SummaryMode.EXAM_FOCUSED: (
        "Requested summary mode: exam_focused. Organize the supplied material for exam "
        "revision, prioritizing definitions, distinctions, formulas, and "
        "problem-solving approaches. Do not claim any topic is guaranteed to appear on "
        "an exam."
    ),
}

EXAM_FOCUS_QUERY_TERMS = (
    "exam preparation key definitions formulas worked examples common problems"
)


class StudyGuideGenerationError(RuntimeError):
    """Study guide generation failed."""


class NoReadyCourseMaterialError(
    StudyGuideGenerationError, CourseMaterialUnavailableError
):
    """The course has no processed material to generate from at all.

    Distinct from a relevance miss: an empty corpus is fixed by uploading and
    processing a document, not by broadening the request.
    """


class InvalidStudyGuideStructureError(
    StudyGuideGenerationError, InvalidGeneratedStructureError
):
    """The provider returned something that is not a valid study guide."""


@dataclass(frozen=True)
class StudyGuideGeneration:
    study_guide: StudyGuideResponse
    material: RetrievedCourseMaterial
    model_used: str
    effective_request: StudyGuideRequest
    profile_knowledge: ProfileKnowledgeContext | None = None
    charge_receipt: ChargeReceipt | None = None


class StudyGuideService:
    PROMPT_TEMPLATE_NAME = "study_guide"
    PROMPT_PATH = (
        Path(__file__).resolve().parents[1] / "app" / "prompts" / "study_guide.json"
    )

    @staticmethod
    def get_course_material(
        db: Session, course_id: int, *, query: str
    ) -> RetrievedCourseMaterial:
        return load_retrieved_material(
            db,
            course_id,
            query=query,
            limit=settings.retrieval_chunk_limit,
            min_similarity=settings.retrieval_min_similarity,
            max_characters=settings.study_guide_material_max_chars,
        )

    @staticmethod
    def build_retrieval_query(course: Course | None, options: StudyGuideRequest) -> str:
        """Turn a generation request into the query retrieval should rank against."""
        return build_retrieval_query(
            course,
            options.topic_focus,
            suffix=(
                EXAM_FOCUS_QUERY_TERMS
                if options.summary_mode is SummaryMode.EXAM_FOCUSED
                else ""
            ),
        )

    @classmethod
    def build_prompt(
        cls,
        course_material: str,
        options: StudyGuideRequest,
        *,
        profile_knowledge: ProfileKnowledgeContext | None = None,
        context: PromptContext,
    ) -> str:
        directive = SUMMARY_FORMAT_DIRECTIVES[options.summary_format]
        return PromptLoader.render(
            cls.PROMPT_TEMPLATE_NAME,
            {
                **context.as_variables(),
                "SUMMARY_FORMAT": (
                    f"Requested summary format: {options.summary_format.value}. "
                    f"{directive}"
                ),
                "SUMMARY_LENGTH": SUMMARY_LENGTH_DIRECTIVES[options.summary_length],
                "DETAIL_LEVEL": DETAIL_LEVEL_DIRECTIVES[options.detail_level],
                "SUMMARY_MODE": SUMMARY_MODE_DIRECTIVES[options.summary_mode],
                "TOPIC_FOCUS": options.topic_focus,
                # Rendered last so course material and profile context can never forge a placeholder
                # that a later substitution would then fill in.
                "TEXT": course_material,
                "PROFILE_CONTEXT": format_profile_context(profile_knowledge),
            },
        )

    @classmethod
    def resolve_effective_request(
        cls, db: Session, course_id: int, request: StudyGuideRequest
    ) -> StudyGuideRequest:
        settings_row = db.scalar(
            select(CourseSettings).where(CourseSettings.course_id == course_id)
        )
        fields_set = request.model_fields_set

        # 1. Summary format
        summary_format = request.summary_format

        # 2. Summary length: explicit request > CourseSettings > request.summary_length
        if "summary_length" in fields_set:
            summary_length = request.summary_length
        elif settings_row is not None and settings_row.summary_length:
            length_raw = settings_row.summary_length.strip().casefold()
            if length_raw == "short":
                summary_length = SummaryLength.SHORT
            elif length_raw == "long":
                summary_length = SummaryLength.LONG
            else:
                summary_length = SummaryLength.MEDIUM
        else:
            summary_length = request.summary_length

        # 3. Detail level: explicit request > CourseSettings > request.detail_level
        if "detail_level" in fields_set:
            detail_level = request.detail_level
        elif settings_row is not None and settings_row.detail_level:
            detail_raw = settings_row.detail_level.strip().casefold()
            if detail_raw in ("concise", "basic"):
                detail_level = DetailLevel.BASIC
            elif detail_raw in ("detailed",):
                detail_level = DetailLevel.DETAILED
            else:
                detail_level = DetailLevel.STANDARD
        else:
            detail_level = request.detail_level

        # 4. Summary mode: explicit request > CourseSettings (study_mode) > request.summary_mode
        if "summary_mode" in fields_set:
            summary_mode = request.summary_mode
        elif settings_row is not None and settings_row.study_mode:
            mode_raw = settings_row.study_mode.strip().casefold()
            if mode_raw in ("exam", "exam_focused", "exam focused"):
                summary_mode = SummaryMode.EXAM_FOCUSED
            else:
                summary_mode = SummaryMode.GENERAL
        else:
            summary_mode = request.summary_mode

        # 5. Topic focus
        topic_focus = request.topic_focus

        return StudyGuideRequest(
            summary_format=summary_format,
            topic_focus=topic_focus,
            summary_length=summary_length,
            detail_level=detail_level,
            summary_mode=summary_mode,
            use_profile_knowledge=request.use_profile_knowledge,
            model=request.model,
        )

    @classmethod
    def generate(
        cls,
        db: Session,
        course_id: int,
        request: StudyGuideRequest,
        provider: TextGenerationProvider,
        *,
        user_id: int | None = None,
    ) -> StudyGuideGeneration:
        effective_request = cls.resolve_effective_request(db, course_id, request)
        course = db.get(Course, course_id)

        resolved_user_id = user_id
        if resolved_user_id is None and course is not None:
            resolved_user_id = course.owner_id

        def log_failure(category: ErrorCategory, **extra) -> None:
            if resolved_user_id:
                AiUsageLogger.log_failure(
                    db,
                    user_id=resolved_user_id,
                    course_id=course_id,
                    generation_type=GenerationType.STUDY_GUIDE,
                    error_category=category,
                    **extra,
                )
                try:
                    db.commit()
                except Exception:
                    db.rollback()

        if count_available_chunks(db, course_id) == 0:
            log_failure(ErrorCategory.NO_READY_MATERIAL)
            raise NoReadyCourseMaterialError(NO_READY_MATERIAL_MESSAGE)

        query = cls.build_retrieval_query(course, effective_request)

        receipt = None
        if resolved_user_id:
            receipt = CreditService.charge(
                db,
                resolved_user_id,
                GENERATION_CREDIT_COSTS["study_guide"],
                source_type="study_guide",
            )
            if receipt is None:
                log_failure(ErrorCategory.INSUFFICIENT_CREDITS)
                raise InsufficientCreditsError("Insufficient credits.")

        try:
            material = cls.get_course_material(db, course_id, query=query)
        except MaterialNotIndexedError:
            db.rollback()
            CreditService.refund(db, receipt)
            log_failure(ErrorCategory.MATERIAL_NOT_INDEXED)
            raise
        except NoRelevantMaterialError:
            db.rollback()
            CreditService.refund(db, receipt)
            log_failure(ErrorCategory.NO_RELEVANT_MATERIAL)
            raise
        except MaterialRetrievalError:
            db.rollback()
            CreditService.refund(db, receipt)
            log_failure(ErrorCategory.RETRIEVAL_ERROR)
            raise
        except Exception:
            db.rollback()
            CreditService.refund(db, receipt)
            raise

        with (
            CreditService.refund_on_error(db, receipt),
            acquire_generation_locks(material.document_ids),
        ):
            generation_ctx = assemble_generation_context(
                db,
                course_id=course_id,
                user_id=resolved_user_id,
                course_material=material,
                include_profile_context=effective_request.use_profile_knowledge,
            )

            prompt_context = resolve_prompt_context(
                db, course=course, user_id=resolved_user_id
            )
            prompt = cls.build_prompt(
                generation_ctx.course_material.text,
                effective_request,
                profile_knowledge=generation_ctx.profile_knowledge,
                context=prompt_context,
            )
            metadata = None

            try:
                if hasattr(provider, "generate_json_with_metadata"):
                    result, metadata = provider.generate_json_with_metadata(prompt)
                else:
                    result = provider.generate_json(prompt)
            except TextGenerationError as exc:
                if resolved_user_id:
                    CreditService.refund(db, receipt)
                log_failure(
                    getattr(exc, "error_category", ErrorCategory.PROVIDER_ERROR)
                )
                raise StudyGuideGenerationError(
                    "Text generation provider failed."
                ) from exc
            except Exception:
                if resolved_user_id:
                    CreditService.refund(db, receipt)
                raise

            try:
                validated = StudyGuideResponse.model_validate(result)
            except ValidationError as exc:
                if resolved_user_id:
                    CreditService.refund(db, receipt)
                log_failure(
                    ErrorCategory.INVALID_STRUCTURE,
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
                effective_request=effective_request,
                profile_knowledge=generation_ctx.profile_knowledge,
                charge_receipt=receipt,
            )

    @staticmethod
    def save_generated_output(
        db: Session,
        course_id: int,
        study_guide: StudyGuideResponse,
        *,
        user_id: int,
        model_used: str,
        generation_settings: str | None = None,
        generation_context: str | None = None,
    ) -> GeneratedOutput:
        return GeneratedOutputService.record(
            db,
            course_id=course_id,
            user_id=user_id,
            output_type="study_guide",
            content=study_guide.model_dump_json(),
            model_used=model_used,
            generation_settings=generation_settings,
            generation_context=generation_context,
        )
