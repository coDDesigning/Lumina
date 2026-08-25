from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.orm import Session

from schemas.ai_usage import ErrorCategory, GenerationType
from schemas.prompt_generator import PromptGenerationResponse
from services.ai_usage_logger import AiUsageLogger
from schemas.prompt_context import PromptContext
from services.prompt_context import resolve_prompt_context
from services.prompt_loader import PromptLoader
from services.text_generation import TextGenerationError, TextGenerationProvider
from services.credits import GENERATION_CREDIT_COSTS, CreditService
from utils.ai_errors import InsufficientCreditsError


class PromptGenerationError(RuntimeError):
    """Prompt generation failed."""


class PromptGeneratorService:
    PROMPT_TEMPLATE_NAME = "prompt_generator"
    PROMPT_PATH = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "prompts"
        / "prompt_generator.json"
    )

    @classmethod
    def build_prompt(
        cls,
        description: str,
        *,
        context: PromptContext | None = None,
    ) -> str:
        resolved = context or PromptContext()
        return PromptLoader.render(
            cls.PROMPT_TEMPLATE_NAME,
            {**resolved.as_variables(), "TEXT": description},
        )

    @classmethod
    def generate(
        cls,
        description: str,
        provider: TextGenerationProvider,
        db: Session | None = None,
        user_id: int | None = None,
        telemetry_provider: str | None = None,
        telemetry_model: str | None = None,
    ) -> PromptGenerationResponse:
        prompt_context = (
            resolve_prompt_context(db, course=None, user_id=user_id)
            if db is not None and user_id is not None
            else PromptContext()
        )
        prompt = cls.build_prompt(description, context=prompt_context)
        metadata = None

        def commit_usage() -> None:
            if db is None:
                return
            try:
                db.commit()
            except Exception:
                db.rollback()

        receipt = None
        if db is not None and user_id is not None:
            receipt = CreditService.charge(
                db,
                user_id,
                GENERATION_CREDIT_COSTS["prompt_generator"],
                source_type="prompt_generator",
            )
            if receipt is None:
                AiUsageLogger.log_failure(
                    db,
                    user_id=user_id,
                    course_id=None,
                    generation_type=GenerationType.PROMPT_GENERATOR,
                    error_category=ErrorCategory.INSUFFICIENT_CREDITS,
                    provider=telemetry_provider,
                    model=telemetry_model,
                )
                commit_usage()
                raise InsufficientCreditsError("Insufficient credits.")

        try:
            if hasattr(provider, "generate_json_with_metadata"):
                result, metadata = provider.generate_json_with_metadata(prompt)
            else:
                result = provider.generate_json(prompt)
        except TextGenerationError as exc:
            if db is not None and user_id is not None:
                CreditService.refund(db, receipt)
                AiUsageLogger.log_failure(
                    db,
                    user_id=user_id,
                    course_id=None,
                    generation_type=GenerationType.PROMPT_GENERATOR,
                    error_category=getattr(
                        exc, "error_category", ErrorCategory.PROVIDER_ERROR
                    ),
                    provider=getattr(exc, "provider", telemetry_provider),
                    model=getattr(exc, "model", telemetry_model),
                )
                commit_usage()
            raise PromptGenerationError("Text generation provider failed.") from exc
        except Exception:
            if db is not None and user_id is not None:
                CreditService.refund(db, receipt)
                AiUsageLogger.log_failure(
                    db,
                    user_id=user_id,
                    course_id=None,
                    generation_type=GenerationType.PROMPT_GENERATOR,
                    error_category=ErrorCategory.UNKNOWN_ERROR,
                    provider=telemetry_provider,
                    model=telemetry_model,
                )
                commit_usage()
            raise

        try:
            validated = PromptGenerationResponse.model_validate(result)
        except ValidationError as exc:
            if db is not None and user_id is not None:
                CreditService.refund(db, receipt)
                AiUsageLogger.log_failure(
                    db,
                    user_id=user_id,
                    course_id=None,
                    generation_type=GenerationType.PROMPT_GENERATOR,
                    error_category=ErrorCategory.INVALID_STRUCTURE,
                    provider=metadata.provider if metadata else telemetry_provider,
                    model=metadata.model if metadata else telemetry_model,
                    latency_ms=metadata.latency_ms if metadata else None,
                )
                commit_usage()
            raise PromptGenerationError(
                "Generated prompt has an invalid structure."
            ) from exc

        if db is not None and user_id is not None:
            AiUsageLogger.log_success(
                db,
                user_id=user_id,
                course_id=None,
                generation_type=GenerationType.PROMPT_GENERATOR,
                metadata=metadata,
                provider=telemetry_provider,
                model=telemetry_model,
            )
            commit_usage()

        return validated
