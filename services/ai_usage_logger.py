import logging
from sqlalchemy.orm import Session

from backend.app.models import AiUsageLog
from schemas.ai_usage import ErrorCategory, GenerationType
from services.text_generation import (
    GenerationMetadata,
    configured_provider_identity,
)

logger = logging.getLogger(__name__)


class AiUsageLogger:
    """Central privacy-safe telemetry logger for AI model interactions.

    Privacy and Safety Guarantee:
    - Never persists raw prompts, chunks, student content, or model response text.
    - Captures only structured telemetry: user_id, course_id, generation type,
      provider, model, token accounting, latency, success flag, and stable error categories.
    - Resilient & best-effort: failures to write telemetry logs are safely caught and logged
      so they never break or corrupt the primary user operation or leak internal errors.
    """

    @classmethod
    def log_usage(
        cls,
        db: Session,
        *,
        user_id: int,
        generation_type: str | GenerationType,
        provider: str | None = None,
        model: str | None = None,
        course_id: int | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        latency_ms: int | None = None,
        success: bool = True,
        error_category: str | ErrorCategory | None = None,
    ) -> AiUsageLog | None:
        """Persist a single structured AI usage telemetry event."""
        if not user_id:
            logger.warning(
                "Skipping AI usage log: user_id is required but was not provided."
            )
            return None

        configured_provider, configured_model = configured_provider_identity()
        provider = provider or configured_provider
        model = model or configured_model

        gen_type_str = (
            generation_type.value
            if isinstance(generation_type, GenerationType)
            else str(generation_type)
        )
        err_cat_str = (
            error_category.value
            if isinstance(error_category, ErrorCategory)
            else str(error_category)
            if error_category is not None
            else None
        )

        log_entry = AiUsageLog(
            user_id=user_id,
            course_id=course_id,
            generation_type=gen_type_str,
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
            success=success,
            error_category=err_cat_str,
        )
        try:
            with db.begin_nested():
                db.add(log_entry)
                db.flush()
            return log_entry
        except Exception as exc:
            logger.warning(
                "Failed to write AI usage telemetry log",
                extra={
                    "event": "ai_usage_write_failed",
                    "exception_type": type(exc).__name__,
                },
            )
            return None

    @classmethod
    def log_success(
        cls,
        db: Session,
        *,
        user_id: int,
        generation_type: str | GenerationType,
        metadata: GenerationMetadata | None = None,
        course_id: int | None = None,
        provider: str | None = None,
        model: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        latency_ms: int | None = None,
    ) -> AiUsageLog | None:
        """Helper to record a successful AI generation event with metadata."""
        if metadata is not None:
            provider = metadata.provider or provider
            model = metadata.model or model
            prompt_tokens = (
                metadata.prompt_tokens
                if metadata.prompt_tokens is not None
                else prompt_tokens
            )
            completion_tokens = (
                metadata.completion_tokens
                if metadata.completion_tokens is not None
                else completion_tokens
            )
            total_tokens = (
                metadata.total_tokens
                if metadata.total_tokens is not None
                else total_tokens
            )
            latency_ms = (
                metadata.latency_ms if metadata.latency_ms is not None else latency_ms
            )

        return cls.log_usage(
            db,
            user_id=user_id,
            generation_type=generation_type,
            provider=provider,
            model=model,
            course_id=course_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
            success=True,
            error_category=None,
        )

    @classmethod
    def log_failure(
        cls,
        db: Session,
        *,
        user_id: int,
        generation_type: str | GenerationType,
        error_category: str | ErrorCategory,
        course_id: int | None = None,
        provider: str | None = None,
        model: str | None = None,
        latency_ms: int | None = None,
    ) -> AiUsageLog | None:
        """Helper to record a failed AI generation event with a stable error category."""
        return cls.log_usage(
            db,
            user_id=user_id,
            generation_type=generation_type,
            provider=provider,
            model=model,
            course_id=course_id,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
            latency_ms=latency_ms,
            success=False,
            error_category=error_category,
        )
