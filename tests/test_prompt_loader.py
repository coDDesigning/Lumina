import json
import pytest

from schemas.prompt_template import (
    MissingPromptVariableError,
    PromptTemplateModel,
    PromptTemplateNotFoundError,
    PromptTemplateSyntaxError,
    PromptTemplateValidationError,
    UnexpectedPromptVariableError,
)
from services.prompt_loader import PromptLoader


# ---------------------------------------------------------------------------
# Parser and Loader Unit Tests
# ---------------------------------------------------------------------------


def test_load_valid_study_guide_template() -> None:
    template = PromptLoader.load_template("study_guide", reload=True)
    assert template.name == "study_guide"
    assert template.version == "1.2.0"
    assert template.required_variables == [
        "TEXT",
        "SUMMARY_FORMAT",
        "TOPIC_FOCUS",
        "SUMMARY_LENGTH",
        "DETAIL_LEVEL",
        "SUMMARY_MODE",
    ]
    assert template.output_schema_ref == "StudyGuideResponse"
    assert len(template.style_constraints) > 0
    assert len(template.safety_constraints) > 0
    assert "{{TEXT}}" in template.template
    assert "{{SUMMARY_FORMAT}}" in template.template
    assert "{{TOPIC_FOCUS}}" in template.template


def test_load_all_built_in_templates() -> None:
    templates = PromptLoader.load_all()
    expected_names = {
        "study_guide",
        "quiz",
        "flashcard",
        "ai_tutor",
        "prompt_generator",
    }
    assert expected_names.issubset(set(templates.keys()))
    for name, template in templates.items():
        assert template.name == name
        assert template.version
        assert template.template


def test_missing_template_file_raises_not_found(tmp_path) -> None:
    with pytest.raises(PromptTemplateNotFoundError) as exc_info:
        PromptLoader.load_template("non_existent_template", directory=tmp_path)
    assert "Prompt template file not found" in str(exc_info.value)


def test_malformed_json_raises_syntax_error(tmp_path) -> None:
    broken_file = tmp_path / "broken.json"
    broken_file.write_text("{ this is not valid json }", encoding="utf-8")

    with pytest.raises(PromptTemplateSyntaxError) as exc_info:
        PromptLoader.load_template("broken", directory=tmp_path, reload=True)
    assert "Malformed JSON" in str(exc_info.value)


def test_missing_required_metadata_raises_validation_error(tmp_path) -> None:
    invalid_file = tmp_path / "invalid.json"
    # Missing required 'template' and 'name' fields
    invalid_file.write_text(json.dumps({"description": "incomplete"}), encoding="utf-8")

    with pytest.raises(PromptTemplateValidationError) as exc_info:
        PromptLoader.load_template("invalid", directory=tmp_path, reload=True)
    assert "Validation failed" in str(exc_info.value)


def test_non_dict_json_raises_validation_error(tmp_path) -> None:
    array_file = tmp_path / "array.json"
    array_file.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")

    with pytest.raises(PromptTemplateValidationError) as exc_info:
        PromptLoader.load_template("array", directory=tmp_path, reload=True)
    assert "must be a JSON object" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Variable Validation & Rendering Tests
# ---------------------------------------------------------------------------


def test_render_template_substitutes_variables() -> None:
    rendered = PromptLoader.render(
        "study_guide",
        {
            "TEXT": "Sample Lecture Notes Content",
            "SUMMARY_FORMAT": "Requested summary format: overview.",
            "TOPIC_FOCUS": "Working Memory",
            "SUMMARY_LENGTH": "Between 200 and 300 words.",
            "DETAIL_LEVEL": "Requested detail level: standard.",
            "SUMMARY_MODE": "Requested summary mode: general.",
        },
        reload=False,
    )
    assert "{{TEXT}}" not in rendered
    assert "{{SUMMARY_FORMAT}}" not in rendered
    assert "{{TOPIC_FOCUS}}" not in rendered
    assert "{{SUMMARY_LENGTH}}" not in rendered
    assert "{{DETAIL_LEVEL}}" not in rendered
    assert "{{SUMMARY_MODE}}" not in rendered
    assert "Sample Lecture Notes Content" in rendered
    assert "Requested summary format: overview." in rendered
    assert "Working Memory" in rendered


def test_render_missing_required_variable_raises_error() -> None:
    with pytest.raises(MissingPromptVariableError) as exc_info:
        PromptLoader.render("study_guide", {})
    assert (
        "missing required variable(s): DETAIL_LEVEL, SUMMARY_FORMAT, SUMMARY_LENGTH, "
        "SUMMARY_MODE, TEXT, TOPIC_FOCUS" in str(exc_info.value)
    )


def test_render_unexpected_extra_variable_raises_error() -> None:
    with pytest.raises(UnexpectedPromptVariableError) as exc_info:
        PromptLoader.render(
            "study_guide",
            {
                "TEXT": "Valid content",
                "SUMMARY_FORMAT": "Requested summary format: overview.",
                "TOPIC_FOCUS": "All Topics",
                "SUMMARY_LENGTH": "Between 200 and 300 words.",
                "DETAIL_LEVEL": "Requested detail level: standard.",
                "SUMMARY_MODE": "Requested summary mode: general.",
                "EXTRA_VAR": "Unexpected content",
                "ANOTHER_EXTRA": "Bad",
            },
        )
    assert "received unexpected variable(s):" in str(exc_info.value)
    assert "EXTRA_VAR" in str(exc_info.value)
    assert "ANOTHER_EXTRA" in str(exc_info.value)


def test_custom_template_with_optional_variables() -> None:
    custom = PromptTemplateModel(
        name="custom_task",
        version="1.0.0",
        required_variables=["REQUIRED_TEXT"],
        optional_variables=["OPTIONAL_HINT"],
        template="Instruction: {{REQUIRED_TEXT}} (Hint: {{OPTIONAL_HINT}})",
    )

    # 1. Provide only required variable
    rendered = custom.render(
        {"REQUIRED_TEXT": "Do something", "OPTIONAL_HINT": "Be quick"}
    )
    assert "Instruction: Do something (Hint: Be quick)" == rendered

    # 2. Extra variable fails
    with pytest.raises(UnexpectedPromptVariableError):
        custom.render({"REQUIRED_TEXT": "Do something", "UNKNOWN": "value"})

    # 3. Missing required variable fails
    with pytest.raises(MissingPromptVariableError):
        custom.render({"OPTIONAL_HINT": "only hint"})


# ---------------------------------------------------------------------------
# Regression Tests for All Migrated Prompts
# ---------------------------------------------------------------------------


def test_quiz_template_regression() -> None:
    rendered = PromptLoader.render(
        "quiz",
        {
            "TEXT": "Linear Algebra Lecture",
            "QUESTION_COUNT": "8",
            "QUESTION_TYPE_DIRECTIVE": "Every question must be multiple choice.",
            "DIFFICULTY_DIRECTIVE": "Every question must be hard.",
            "TOPIC_FOCUS": "Eigenvalues",
        },
    )
    assert "Linear Algebra Lecture" in rendered
    assert "{{TEXT}}" not in rendered
    assert "{{QUESTION_COUNT}}" not in rendered
    assert "{{QUESTION_TYPE_DIRECTIVE}}" not in rendered
    assert "{{DIFFICULTY_DIRECTIVE}}" not in rendered
    assert "{{TOPIC_FOCUS}}" not in rendered
    assert "Generate exactly 8 questions" in rendered
    assert "Every question must be multiple choice." in rendered
    assert "Every question must be hard." in rendered
    assert "Eigenvalues" in rendered

    template = PromptLoader.load_template("quiz")
    assert template.output_schema_ref == "QuizGenerationResponse"
    assert template.required_variables == [
        "TEXT",
        "QUESTION_COUNT",
        "QUESTION_TYPE_DIRECTIVE",
        "DIFFICULTY_DIRECTIVE",
        "TOPIC_FOCUS",
    ]


def test_flashcard_template_regression() -> None:
    rendered = PromptLoader.render(
        "flashcard",
        {"TEXT": "Data Structures Notes"},
    )
    assert "Data Structures Notes" in rendered
    assert "{{TEXT}}" not in rendered
    assert "flashcards" in rendered.lower()

    template = PromptLoader.load_template("flashcard")
    assert template.output_schema_ref == "FlashcardGenerationResponse"
    assert template.required_variables == ["TEXT"]


def test_ai_tutor_template_regression() -> None:
    rendered = PromptLoader.render(
        "ai_tutor",
        {
            "COURSE_MATERIAL": "Operating Systems Virtual Memory",
            "QUESTION": "What is page fault?",
        },
    )
    assert "Operating Systems Virtual Memory" in rendered
    assert "What is page fault?" in rendered
    assert "{{COURSE_MATERIAL}}" not in rendered
    assert "{{QUESTION}}" not in rendered

    template = PromptLoader.load_template("ai_tutor")
    assert template.output_schema_ref == "AiTutorResponse"
    assert set(template.required_variables) == {"COURSE_MATERIAL", "QUESTION"}


def test_prompt_generator_template_regression() -> None:
    rendered = PromptLoader.render(
        "prompt_generator",
        {"TEXT": "Help me write a Python script for web scraping"},
    )
    assert "Help me write a Python script for web scraping" in rendered
    assert "{{TEXT}}" not in rendered
    assert "generated_prompt" in rendered

    template = PromptLoader.load_template("prompt_generator")
    assert template.output_schema_ref == "PromptGenerationResponse"
    assert template.required_variables == ["TEXT"]
