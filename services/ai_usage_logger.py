import logging
import math

from sqlalchemy.orm import Session

from backend.app.config import MAX_AI_EVENT_ESTIMATED_COST_USD, settings
from backend.app.models import AiUsageLog
from backend.app.observability import emit_emf_metrics
from schemas.ai_usage import ErrorCategory, GenerationType
from services.text_generation import (
    GenerationMetadata,
    configured_provider_identity,
)

logger = logging.getLogger(__name__)


def _estimate_cost(
    provider: str,
    model: str,
    prompt_tokens: int | None,
    completion_tokens: int | None,
) -> tuple[float | None, str | None]:
    if (
        prompt_tokens is None
        or completion_tokens is None
        or prompt_tokens < 0
        or completion_tokens < 0
        or settings.ai_pricing_version is None
    ):
        return None, None

    rates = settings.ai_model_cost_rates.get(f"{provider}:{model}")
    if rates is None:
        return None, None
    try:
        estimated_cost = (
            prompt_tokens * rates["prompt_usd_per_million_tokens"]
            + completion_tokens * rates["completion_usd_per_million_tokens"]
        ) / 1_000_000
    except OverflowError:
        return None, None
    if (
        not math.isfinite(estimated_cost)
        or estimated_cost > MAX_AI_EVENT_ESTIMATED_COST_USD
    ):
        return None, None
    return round(estimated_cost, 12), settings.ai_pricing_version


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
        estimated_cost_usd, pricing_version = None, None
        if success:
            try:
                estimated_cost_usd, pricing_version = _estimate_cost(
                    provider,
                    model,
                    prompt_tokens,
                    completion_tokens,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to estimate AI usage cost",
                    extra={
                        "event": "ai_usage_cost_estimate_failed",
                        "exception_type": type(exc).__name__,
                    },
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
            estimated_cost_usd=estimated_cost_usd,
            pricing_version=pricing_version,
        )
        try:
            metrics: dict[str, float | int] = {"ProviderCalls": 1}
            units: dict[str, str] = {"ProviderCalls": "Count"}
            if latency_ms is not None and latency_ms >= 0:
                metrics["ProviderLatencyMs"] = latency_ms
                units["ProviderLatencyMs"] = "Milliseconds"
            if not success:
                metrics["ProviderErrors"] = 1
                units["ProviderErrors"] = "Count"
            emit_emf_metrics(
                metrics,
                dimensions={
                    "Service": "api",
                    "Environment": settings.app_env,
                    "Provider": provider,
                },
                units=units,
                namespace="Lumina/AI",
            )
            if not success and err_cat_str:
                emit_emf_metrics(
                    {"ProviderErrors": 1},
                    dimensions={
                        "Service": "api",
                        "Environment": settings.app_env,
                        "Provider": provider,
                        "ErrorCategory": err_cat_str,
                    },
                    namespace="Lumina/AI",
                )
            if not success:
                emit_emf_metrics(
                    {"ProviderErrors": 1},
                    dimensions={
                        "Service": "api",
                        "Environment": settings.app_env,
                    },
                    namespace="Lumina/AI",
                )
        except Exception:
            logger.warning(
                "Failed to emit AI provider health metrics",
                extra={"event": "ai_metrics_emit_failed"},
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
