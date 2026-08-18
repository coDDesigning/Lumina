from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PromptTemplateError(RuntimeError):
    """Base error for prompt template operations."""


class PromptTemplateNotFoundError(PromptTemplateError):
    """The requested prompt template file was not found."""


class PromptTemplateSyntaxError(PromptTemplateError):
    """The prompt template file contains invalid JSON."""


class PromptTemplateValidationError(PromptTemplateError):
    """The prompt template metadata or structure failed validation."""


class MissingPromptVariableError(PromptTemplateError):
    """A required prompt variable was not provided."""


class UnexpectedPromptVariableError(PromptTemplateError):
    """An unexpected variable was provided to the prompt template."""


class PromptTemplateModel(BaseModel):
    """Structured, versioned definition of an AI task prompt template."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        ...,
        description="Unique identifier for the prompt task (e.g. 'study_guide')",
    )
    version: str = Field(
        ...,
        description="Semantic version of the template (e.g. '1.0.0')",
    )
    description: str | None = Field(
        default=None,
        description="Human-readable description of the template purpose",
    )
    required_variables: list[str] = Field(
        default_factory=list,
        description="List of mandatory variable names that must be provided",
    )
    optional_variables: list[str] = Field(
        default_factory=list,
        description="List of optional variable names that may be provided",
    )
    output_schema_ref: str | None = Field(
        default=None,
        description="Reference to the Pydantic response model for output validation",
    )
    style_constraints: list[str] = Field(
        default_factory=list,
        description="Style and formatting guidelines enforced for the output",
    )
    safety_constraints: list[str] = Field(
        default_factory=list,
        description="Safety and truthfulness constraints (e.g. no hallucinations)",
    )
    model_hints: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional provider/model hints such as temperature or preferred model",
    )
    system_instruction: str | None = Field(
        default=None,
        description="Optional system role instructions",
    )
    template: str = Field(
        ...,
        description="Prompt template body containing {{VARIABLE}} placeholders",
    )

    def validate_variables(self, variables: dict[str, Any]) -> None:
        """Validate that all required variables are present and no unexpected variables exist."""
        provided_keys = set(variables.keys())
        required_keys = set(self.required_variables)
        allowed_keys = required_keys | set(self.optional_variables)

        missing = required_keys - provided_keys
        if missing:
            raise MissingPromptVariableError(
                f"Prompt template '{self.name}' is missing required variable(s): "
                f"{', '.join(sorted(missing))}"
            )

        unexpected = provided_keys - allowed_keys
        if unexpected:
            raise UnexpectedPromptVariableError(
                f"Prompt template '{self.name}' received unexpected variable(s): "
                f"{', '.join(sorted(unexpected))}"
            )

    def render(self, variables: dict[str, Any]) -> str:
        """Validate variables and render the template by substituting {{VARIABLE}} placeholders."""
        self.validate_variables(variables)

        rendered = self.template
        for key, value in variables.items():
            placeholder = f"{{{{{key}}}}}"
            rendered = rendered.replace(placeholder, str(value))

        return rendered
