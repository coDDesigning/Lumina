from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.orm import Session

from schemas.ai_usage import ErrorCategory, GenerationType
from schemas.prompt_generator import PromptGenerationResponse
from services.ai_usage_logger import AiUsageLogger
from services.prompt_loader import PromptLoader
from services.text_generation import TextGenerationError, TextGenerationProvider


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
    ) -> str:
        return PromptLoader.render(cls.PROMPT_TEMPLATE_NAME, {"TEXT": description})

    @classmethod
    def generate(
        cls,
        description: str,
        provider: TextGenerationProvider,
        db: Session | None = None,
        user_id: int | None = None,
    ) -> PromptGenerationResponse:
        prompt = cls.build_prompt(description)
        metadata = None

        try:
            if hasattr(provider, "generate_json_with_metadata"):
                result, metadata = provider.generate_json_with_metadata(prompt)
            else:
                result = provider.generate_json(prompt)
        except TextGenerationError as exc:
            if db is not None and user_id is not None:
                AiUsageLogger.log_failure(
                    db,
                    user_id=user_id,
                    course_id=None,
                    generation_type=GenerationType.PROMPT_GENERATOR,
                    error_category=getattr(
                        exc, "error_category", ErrorCategory.PROVIDER_ERROR
                    ),
                )
            raise PromptGenerationError("Text generation provider failed.") from exc

        try:
            validated = PromptGenerationResponse.model_validate(result)
        except ValidationError as exc:
            if db is not None and user_id is not None:
                AiUsageLogger.log_failure(
                    db,
                    user_id=user_id,
                    course_id=None,
                    generation_type=GenerationType.PROMPT_GENERATOR,
                    error_category=ErrorCategory.INVALID_STRUCTURE,
                    latency_ms=metadata.latency_ms if metadata else None,
                )
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
            )

        return validated
