from pathlib import Path

from pydantic import ValidationError

from schemas.prompt_generator import PromptGenerationResponse
from services.text_generation import TextGenerationError, TextGenerationProvider


class PromptGenerationError(RuntimeError):
    """Prompt generation failed."""


class PromptGeneratorService:
    PROMPT_PATH = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "prompts"
        / "prompt_generator_prompt.txt"
    )

    @classmethod
    def build_prompt(
        cls,
        description: str,
    ) -> str:
        prompt_template = cls.PROMPT_PATH.read_text(
            encoding="utf-8",
        )

        return prompt_template.replace(
            "{{TEXT}}",
            description,
        )

    @classmethod
    def generate(
        cls,
        description: str,
        provider: TextGenerationProvider,
    ) -> PromptGenerationResponse:
        prompt = cls.build_prompt(description)

        try:
            result = provider.generate_json(prompt)
        except TextGenerationError as exc:
            raise PromptGenerationError("Text generation provider failed.") from exc

        try:
            return PromptGenerationResponse.model_validate(result)
        except ValidationError as exc:
            raise PromptGenerationError(
                "Generated prompt has an invalid structure."
            ) from exc
