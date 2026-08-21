# services/prompt_loader.py
"""Central loader, validator, and renderer for structured, versioned AI prompt templates."""

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from schemas.prompt_template import (
    MissingPromptVariableError,
    PromptTemplateDeferredError,
    PromptTemplateModel,
    PromptTemplateNotFoundError,
    PromptTemplateSyntaxError,
    PromptTemplateValidationError,
    UnexpectedPromptVariableError,
)

__all__ = [
    "PromptLoader",
    "PromptTemplateModel",
    "PromptTemplateDeferredError",
    "PromptTemplateNotFoundError",
    "PromptTemplateSyntaxError",
    "PromptTemplateValidationError",
    "MissingPromptVariableError",
    "UnexpectedPromptVariableError",
]


class PromptLoader:
    """Central loader and validator for structured, versioned AI prompt templates."""

    DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "app" / "prompts"
    _cache: dict[str, PromptTemplateModel] = {}

    @classmethod
    def get_template_path(cls, name: str, directory: Path | None = None) -> Path:
        """Resolve the file path for a prompt template name."""
        base_dir = directory or cls.DEFAULT_PROMPTS_DIR
        file_name = name if name.endswith(".json") else f"{name}.json"
        return base_dir / file_name

    @classmethod
    def load_template(
        cls,
        name: str,
        directory: Path | None = None,
        reload: bool = False,
    ) -> PromptTemplateModel:
        """Load and validate a structured prompt template by name."""
        template_key = f"{directory or cls.DEFAULT_PROMPTS_DIR}:{name}"
        if not reload and template_key in cls._cache:
            return cls._cache[template_key]

        path = cls.get_template_path(name, directory)
        if not path.is_file():
            raise PromptTemplateNotFoundError(
                f"Prompt template file not found: '{path}'"
            )

        try:
            content = path.read_text(encoding="utf-8")
        except Exception as exc:
            raise PromptTemplateNotFoundError(
                f"Failed to read prompt template at '{path}': {exc}"
            ) from exc

        try:
            raw_data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise PromptTemplateSyntaxError(
                f"Malformed JSON in prompt template '{path}': {exc}"
            ) from exc

        if not isinstance(raw_data, dict):
            raise PromptTemplateValidationError(
                f"Prompt template in '{path}' must be a JSON object, got {type(raw_data).__name__}"
            )

        try:
            template = PromptTemplateModel.model_validate(raw_data)
        except ValidationError as exc:
            raise PromptTemplateValidationError(
                f"Validation failed for prompt template '{path}': {exc}"
            ) from exc

        cls._cache[template_key] = template
        return template

    @classmethod
    def render(
        cls,
        name: str,
        variables: dict[str, Any],
        directory: Path | None = None,
        reload: bool = False,
        *,
        allow_deferred: bool = False,
    ) -> str:
        """Load a prompt template by name, validate variables, and return rendered prompt.

        A deferred template is refused unless the caller opts in explicitly. Only
        rendering is guarded: `load_template` and `load_all` keep the whole catalog
        introspectable so tooling and tests can read a deferred template's metadata.
        """
        template = cls.load_template(name, directory=directory, reload=reload)
        if template.status == "deferred" and not allow_deferred:
            raise PromptTemplateDeferredError(
                f"Prompt template '{template.name}' is deferred and must not be "
                f"rendered in production: {template.deferral_reason}"
            )
        return template.render(variables)

    @classmethod
    def get_render_metadata(
        cls,
        name: str,
        variables: dict[str, Any],
        directory: Path | None = None,
    ) -> dict[str, Any]:
        """Produce privacy-safe telemetry/observability metadata about prompt rendering.

        Never logs raw prompt content, chunks, questions, or answers.
        """
        template = cls.load_template(name, directory=directory)

        return {
            "template_name": template.name,
            "template_version": template.version,
            "output_schema_ref": template.output_schema_ref,
            "applied_variables": sorted(list(variables.keys())),
        }

    @classmethod
    def load_all(cls, directory: Path | None = None) -> dict[str, PromptTemplateModel]:
        """Load and validate all JSON prompt templates found in the specified directory."""
        base_dir = directory or cls.DEFAULT_PROMPTS_DIR
        templates: dict[str, PromptTemplateModel] = {}

        if not base_dir.is_dir():
            return templates

        for file_path in base_dir.glob("*.json"):
            if file_path.name in ("config.json", "messages.json"):
                continue
            template = cls.load_template(file_path.stem, directory=base_dir)
            templates[template.name] = template

        return templates

    @classmethod
    def clear_cache(cls) -> None:
        """Clear the in-memory template cache."""
        cls._cache.clear()
