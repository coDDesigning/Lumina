# tests/test_prompt_loader.py
"""Deterministic unit and regression tests for Lumina's Learner-Aware Prompt Library.

Covers:
- Template loading, parsing, and strict metadata validation
- Education level prompt adaptation (high_school, undergraduate, graduate,
  professional_other, unspecified)
- Deterministic rendering of every production template with sample inputs
- Deterministic rendering of all production templates with zero unresolved placeholders
- Observability and privacy-safe render metadata generation
"""

import importlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from schemas.prompt_context import (
    EDUCATION_LEVEL_DIRECTIVES,
    EducationLevel,
    PromptContext,
)
from schemas.prompt_template import (
    MissingPromptVariableError,
    PromptTemplateDeferredError,
    PromptTemplateModel,
    PromptTemplateNotFoundError,
    PromptTemplateSyntaxError,
    PromptTemplateValidationError,
    UnexpectedPromptVariableError,
)
from services.prompt_components import (
    build_grounding_block,
    build_safety_block,
)
from services.prompt_loader import PromptLoader


def shared_variables(level: EducationLevel, course_title: str = "AP Biology"):
    return PromptContext(
        education_level=level, course_title=course_title, subject_area="Biology"
    ).as_variables()


SHARED_PROMPT_VARIABLES = {
    "EDUCATION_LEVEL": "high_school. Write for a secondary-school learner.",
    "COURSE_TITLE": "AP Biology",
    "SUBJECT_AREA": "Biology",
    "MATERIAL_KIND": "textbook. The material is textbook content.",
}


# ---------------------------------------------------------------------------
# Parser and Loader Unit Tests
# ---------------------------------------------------------------------------


def test_load_valid_study_guide_template() -> None:
    template = PromptLoader.load_template("study_guide", reload=True)
    assert template.name == "study_guide"
    assert template.version == "2.3.0"
    assert template.required_variables == [
        "EDUCATION_LEVEL",
        "COURSE_TITLE",
        "SUBJECT_AREA",
        "MATERIAL_KIND",
        "TEXT",
        "SUMMARY_FORMAT",
        "TOPIC_FOCUS",
        "SUMMARY_LENGTH",
        "DETAIL_LEVEL",
        "SUMMARY_MODE",
        "PROFILE_CONTEXT",
    ]
    assert template.required_variables[:4] == [
        "EDUCATION_LEVEL",
        "COURSE_TITLE",
        "SUBJECT_AREA",
        "MATERIAL_KIND",
    ]
    assert template.output_schema_ref == "StudyGuideResponse"
    assert len(template.style_constraints) > 0
    assert len(template.safety_constraints) > 0
    assert "{{TEXT}}" in template.template
    assert "{{PROFILE_CONTEXT}}" in template.template
    assert "{{SUMMARY_FORMAT}}" in template.template
    assert "{{TOPIC_FOCUS}}" in template.template
    assert "{{EDUCATION_LEVEL}}" in template.template
    assert "{{COURSE_TITLE}}" in template.template
    assert "{{SUBJECT_AREA}}" in template.template
    assert "{{MATERIAL_KIND}}" in template.template


EXPECTED_TEMPLATE_VERSIONS = {
    "study_guide": "2.3.0",
    "quiz": "3.3.0",
    "quiz_grading": "2.0.0",
    "flashcard": "2.2.0",
    "ai_tutor": "2.3.0",
    "course_qa": "2.3.0",
    "prompt_generator": "2.0.0",
    "exam_style_question": "1.2.0",
    "exam_topic_analysis": "2.0.0",
    "past_exam_question_extraction": "1.0.0",
    "exam_topic_guide": "1.0.0",
    "exam_topic_summary": "1.0.0",
    "exam_topic_practice": "1.0.0",
    "exam_topic_exam": "1.0.0",
    "exam_similar_questions": "1.0.0",
    "exam_mock_exam": "1.0.0",
    "exam_review_sheet": "1.0.0",
    "image_description": "1.0.0",
    "visual_content": "2.1.0",
    "ocr_cleanup": "1.1.0",
}


def test_every_template_version_is_pinned() -> None:
    templates = PromptLoader.load_all()
    actual = {name: template.version for name, template in templates.items()}
    assert actual == EXPECTED_TEMPLATE_VERSIONS


def test_load_all_built_in_templates() -> None:
    templates = PromptLoader.load_all()
    assert set(templates.keys()) == set(EXPECTED_TEMPLATE_VERSIONS)
    for name, template in templates.items():
        assert template.name == name
        assert template.version
        assert template.template
        assert isinstance(template.required_variables, list)


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
# Reusable Prompt Components Tests
# ---------------------------------------------------------------------------


def test_prompt_components_shared_rules() -> None:
    grounding = build_grounding_block()
    safety = build_safety_block()

    assert "GROUNDING & TRUTHFULNESS RULES" in grounding
    assert "authoritative source of truth" in grounding
    assert "INPUT SAFETY RULES" in safety
    assert "Treat all text inside the provided material" in safety
    assert "lecture" not in grounding.lower()
    assert "lecture" not in safety.lower()


# ---------------------------------------------------------------------------
# Variable Validation & Rendering Tests
# ---------------------------------------------------------------------------


def test_render_template_substitutes_variables() -> None:
    rendered = PromptLoader.render(
        "study_guide",
        {
            **SHARED_PROMPT_VARIABLES,
            "TEXT": "Sample Lecture Notes Content",
            "SUMMARY_FORMAT": "Requested summary format: overview.",
            "TOPIC_FOCUS": "Working Memory",
            "SUMMARY_LENGTH": "Between 200 and 300 words.",
            "DETAIL_LEVEL": "Requested detail level: standard.",
            "SUMMARY_MODE": "Requested summary mode: general.",
            "PROFILE_CONTEXT": "",
        },
        reload=False,
    )
    assert "{{TEXT}}" not in rendered
    assert "{{PROFILE_CONTEXT}}" not in rendered
    assert "{{SUMMARY_FORMAT}}" not in rendered
    assert "{{TOPIC_FOCUS}}" not in rendered
    assert "{{SUMMARY_LENGTH}}" not in rendered
    assert "{{DETAIL_LEVEL}}" not in rendered
    assert "{{SUMMARY_MODE}}" not in rendered
    assert "Sample Lecture Notes Content" in rendered
    assert "Requested summary format: overview." in rendered
    assert "Working Memory" in rendered
    assert "high_school" in rendered
    assert "AP Biology" in rendered


def test_render_missing_required_variable_raises_error() -> None:
    with pytest.raises(MissingPromptVariableError) as exc_info:
        PromptLoader.render("study_guide", {})
    assert (
        "missing required variable(s): COURSE_TITLE, DETAIL_LEVEL, "
        "EDUCATION_LEVEL, MATERIAL_KIND, PROFILE_CONTEXT, SUBJECT_AREA, SUMMARY_FORMAT, "
        "SUMMARY_LENGTH, SUMMARY_MODE, TEXT, TOPIC_FOCUS" in str(exc_info.value)
    )


def test_render_unexpected_extra_variable_raises_error() -> None:
    with pytest.raises(UnexpectedPromptVariableError) as exc_info:
        PromptLoader.render(
            "study_guide",
            {
                **SHARED_PROMPT_VARIABLES,
                "TEXT": "Valid content",
                "SUMMARY_FORMAT": "Requested summary format: overview.",
                "TOPIC_FOCUS": "All Topics",
                "SUMMARY_LENGTH": "Between 200 and 300 words.",
                "DETAIL_LEVEL": "Requested detail level: standard.",
                "SUMMARY_MODE": "Requested summary mode: general.",
                "PROFILE_CONTEXT": "",
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

    rendered = custom.render(
        {"REQUIRED_TEXT": "Do something", "OPTIONAL_HINT": "Be quick"}
    )
    assert "Instruction: Do something (Hint: Be quick)" == rendered

    with pytest.raises(UnexpectedPromptVariableError):
        custom.render({"REQUIRED_TEXT": "Do something", "UNKNOWN": "value"})

    with pytest.raises(MissingPromptVariableError):
        custom.render({"OPTIONAL_HINT": "only hint"})


def test_omitted_optional_variable_never_reaches_a_rendered_prompt() -> None:
    custom = PromptTemplateModel(
        name="custom_task",
        version="1.0.0",
        required_variables=["REQUIRED_TEXT"],
        optional_variables=["OPTIONAL_HINT"],
        template="Instruction: {{REQUIRED_TEXT}} (Hint: {{OPTIONAL_HINT}})",
    )

    with pytest.raises(MissingPromptVariableError) as excinfo:
        custom.render({"REQUIRED_TEXT": "Do something"})

    assert "OPTIONAL_HINT" in str(excinfo.value)


def test_template_declaring_an_undeclared_placeholder_fails_to_load() -> None:
    with pytest.raises(ValidationError):
        PromptTemplateModel(
            name="typo_task",
            version="1.0.0",
            required_variables=["EDUCATION_LEVEL"],
            template="Level: {{EDUCATON_LEVEL}}",
        )


def test_declared_variable_absent_from_the_body_is_allowed() -> None:
    custom = PromptTemplateModel(
        name="sparse_task",
        version="1.0.0",
        required_variables=["USED", "UNUSED"],
        template="Only {{USED}} appears.",
    )

    assert custom.render({"USED": "this", "UNUSED": "that"}) == "Only this appears."


def test_a_value_substituted_last_can_never_forge_a_placeholder() -> None:
    custom = PromptTemplateModel(
        name="forge_task",
        version="1.0.0",
        required_variables=["MATERIAL", "SETTING"],
        template="{{MATERIAL}} then {{SETTING}}",
    )

    rendered = custom.render({"SETTING": "value", "MATERIAL": "{{SETTING}}"})

    assert rendered == "{{SETTING}} then value"


def test_every_built_in_template_renders_without_leaving_a_placeholder() -> None:
    for name, template in PromptLoader.load_all().items():
        variables = {
            variable: f"<{variable.lower()}>"
            for variable in sorted(template.template_placeholders())
        }
        rendered = template.render(variables)
        for variable in variables:
            assert f"{{{{{variable}}}}}" not in rendered, name


# ---------------------------------------------------------------------------
# Education Level Adaptation Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "level",
    [
        EducationLevel.HIGH_SCHOOL,
        EducationLevel.UNDERGRADUATE,
        EducationLevel.GRADUATE,
        EducationLevel.PROFESSIONAL_OTHER,
        EducationLevel.UNSPECIFIED,
    ],
)
def test_study_guide_adaptation_across_education_levels(level: EducationLevel) -> None:
    rendered = PromptLoader.render(
        "study_guide",
        {
            **shared_variables(level, course_title="Chemistry"),
            "TEXT": "Thermodynamics principles...",
            "SUMMARY_FORMAT": "Requested summary format: comprehensive.",
            "TOPIC_FOCUS": "Entropy",
            "SUMMARY_LENGTH": "Between 400 and 600 words.",
            "DETAIL_LEVEL": "Requested detail level: detailed.",
            "SUMMARY_MODE": "Requested summary mode: general.",
            "PROFILE_CONTEXT": "",
        },
    )
    assert "{{" not in rendered
    assert "}}" not in rendered
    assert "Chemistry" in rendered
    assert level.value in rendered
    assert EDUCATION_LEVEL_DIRECTIVES[level] in rendered

    for other in EducationLevel:
        if other is not level:
            assert EDUCATION_LEVEL_DIRECTIVES[other] not in rendered


def test_quiz_adapts_to_the_shared_context() -> None:
    rendered = PromptLoader.render(
        "quiz",
        {
            **shared_variables(
                EducationLevel.HIGH_SCHOOL, course_title="Newtonian Mechanics"
            ),
            "QUESTION_COUNT": "5",
            "QUESTION_TYPES_DIRECTIVE": "Use only multiple_choice.",
            "QUESTION_SCHEMAS": '{"question_type": "multiple_choice"}',
            "DIFFICULTY_DIRECTIVE": "Standard high school physics level.",
            "REQUESTED_DIFFICULTY": "medium",
            "TOPIC_FOCUS": "Newton's First Law",
            "TEXT": "Physics Lecture Notes...",
            "PROFILE_CONTEXT": "",
        },
    )
    assert "high_school" in rendered
    assert "Newtonian Mechanics" in rendered
    assert "Generate exactly 5 questions" in rendered
    assert "{{" not in rendered


def test_ai_tutor_adapts_to_the_shared_context() -> None:
    rendered = PromptLoader.render(
        "ai_tutor",
        {
            **shared_variables(EducationLevel.GRADUATE),
            "COURSE_MATERIAL": "Advanced Operating Systems",
            "CONVERSATION_HISTORY": "User: What is RCU?\nAssistant: Read-Copy Update.",
            "QUESTION": "How does grace period detection work in preemptible RCU?",
            "PROFILE_CONTEXT": "",
        },
    )
    assert "graduate" in rendered
    assert EDUCATION_LEVEL_DIRECTIVES[EducationLevel.GRADUATE] in rendered
    assert "Advanced Operating Systems" in rendered
    assert "grace period detection" in rendered
    assert "{{" not in rendered


def test_course_qa_adapts_to_the_shared_context() -> None:
    rendered = PromptLoader.render(
        "course_qa",
        {
            **shared_variables(EducationLevel.UNDERGRADUATE),
            "COURSE_MATERIAL": "Computer Networks Notes",
            "CONVERSATION_HISTORY": "",
            "QUESTION": "What is the difference between TCP and UDP?",
            "PROFILE_CONTEXT": "",
        },
    )
    assert "undergraduate" in rendered
    assert EDUCATION_LEVEL_DIRECTIVES[EducationLevel.UNDERGRADUATE] in rendered
    assert "Computer Networks Notes" in rendered
    assert rendered.count("- Adapt terminology, explanation depth, and clarity") == 1
    assert "Adapt explanation clarity and terminology" not in rendered
    assert "{{" not in rendered


# ---------------------------------------------------------------------------
# Observability & Metadata Tests
# ---------------------------------------------------------------------------


def test_get_render_metadata_returns_safe_telemetry() -> None:
    meta = PromptLoader.get_render_metadata(
        "study_guide",
        {
            "TEXT": "Sensitive raw course material",
            "SUMMARY_FORMAT": "format",
            "TOPIC_FOCUS": "focus",
            "SUMMARY_LENGTH": "length",
            "DETAIL_LEVEL": "detail",
            "SUMMARY_MODE": "mode",
            "PROFILE_CONTEXT": "",
        },
    )
    assert meta["template_name"] == "study_guide"
    assert meta["template_version"] == "2.3.0"
    assert meta["output_schema_ref"] == "StudyGuideResponse"
    assert "TEXT" in meta["applied_variables"]

    meta_str = json.dumps(meta)
    assert "Sensitive raw course material" not in meta_str


# ---------------------------------------------------------------------------
# Grounding & Anti-Hallucination Regression Tests
# Prompt neutrality lives in tests/test_prompt_neutrality.py
# ---------------------------------------------------------------------------


def test_all_grounded_prompts_state_source_material_authority() -> None:
    """Ensure every content-generating template explicitly instructs source grounding."""
    templates = PromptLoader.load_all()
    grounded_templates = [
        "study_guide",
        "quiz",
        "flashcard",
        "ai_tutor",
        "course_qa",
        "visual_content",
    ]

    for name in grounded_templates:
        assert name in templates
        template = templates[name]
        text = template.template.lower()
        # Must contain grounding rules
        assert (
            "only" in text
            or "primary source" in text
            or "sole source" in text
            or "ground" in text
        ), f"Template '{name}' is missing explicit source grounding rules"


SAMPLE_TEMPLATE_INPUTS = {
    "study_guide": {
        **SHARED_PROMPT_VARIABLES,
        "TEXT": "Course material text",
        "SUMMARY_FORMAT": "Format directive",
        "TOPIC_FOCUS": "All Topics",
        "SUMMARY_LENGTH": "Medium",
        "DETAIL_LEVEL": "Standard",
        "SUMMARY_MODE": "General",
        "PROFILE_CONTEXT": "",
    },
    "quiz": {
        **SHARED_PROMPT_VARIABLES,
        "TEXT": "Course material text",
        "QUESTION_COUNT": "5",
        "QUESTION_TYPES_DIRECTIVE": "Types",
        "QUESTION_SCHEMAS": "Schemas",
        "REQUESTED_DIFFICULTY": "medium",
        "DIFFICULTY_DIRECTIVE": "Difficulty directive",
        "TOPIC_FOCUS": "All Topics",
        "PROFILE_CONTEXT": "",
    },
    "quiz_grading": {
        **SHARED_PROMPT_VARIABLES,
        "SUBMISSION_COUNT": "1",
        "SUBMISSIONS": "Submissions text",
    },
    "exam_topic_analysis": {
        **SHARED_PROMPT_VARIABLES,
        "TOPIC_FOCUS": "All Topics",
        "DECLARED_TOPICS": "Declared topics",
        "SYLLABUS_TEXT": "Syllabus text",
        "TEXT": "Course material text",
    },
    "past_exam_question_extraction": {
        **SHARED_PROMPT_VARIABLES,
        "TEXT": "Examination paper text",
    },
    "exam_topic_guide": {
        **SHARED_PROMPT_VARIABLES,
        "TOPIC_LABEL": "Graph Traversal",
        "PRIORITY_NOTE": "Priority note",
        "TEXT": "Course material text",
    },
    "exam_topic_summary": {
        **SHARED_PROMPT_VARIABLES,
        "TOPIC_LABEL": "Graph Traversal",
        "PRIORITY_NOTE": "Priority note",
        "TEXT": "Course material text",
    },
    "exam_topic_practice": {
        **SHARED_PROMPT_VARIABLES,
        "TOPIC_LABEL": "Graph Traversal",
        "QUESTION_COUNT": "10",
        "DIFFICULTY_DIRECTIVE": "Mixed difficulty directive",
        "QUESTION_TYPE_DIRECTIVES": "Type directives",
        "QUESTION_TYPE_SCHEMAS": "Type schemas",
        "TEXT": "Course material text",
    },
    "exam_topic_exam": {
        **SHARED_PROMPT_VARIABLES,
        "TOPIC_LABEL": "Graph Traversal",
        "QUESTION_COUNT": "10",
        "DIFFICULTY_DIRECTIVE": "Mixed difficulty directive",
        "QUESTION_TYPE_DIRECTIVES": "Type directives",
        "QUESTION_TYPE_SCHEMAS": "Type schemas",
        "PAST_QUESTION_STYLE": "Past question style",
        "TEXT": "Course material text",
    },
    "exam_similar_questions": {
        **SHARED_PROMPT_VARIABLES,
        "TOPIC_LABEL": "Graph Traversal",
        "ORIGINAL_QUESTIONS": "1. Explain breadth-first search.",
        "TEXT": "Course material text",
    },
    "exam_mock_exam": {
        **SHARED_PROMPT_VARIABLES,
        "PLAN_TOPICS": "- Graph Traversal (weight 2)",
        "PAST_QUESTION_STYLE": "Past question style",
        "QUESTION_COUNT": "20",
        "QUESTION_TYPE_DIRECTIVES": "Type directives",
        "QUESTION_TYPE_SCHEMAS": "Type schemas",
        "TEXT": "Course material text",
    },
    "exam_review_sheet": {
        **SHARED_PROMPT_VARIABLES,
        "PLAN_TOPICS": "- Graph Traversal (weight 2)",
        "TEXT": "Course material text",
    },
    "flashcard": {
        **SHARED_PROMPT_VARIABLES,
        "TEXT": "Course material text",
        "TOPIC_FOCUS": "All Topics",
        "PROFILE_CONTEXT": "",
    },
    "ai_tutor": {
        **SHARED_PROMPT_VARIABLES,
        "COURSE_MATERIAL": "Material",
        "CONVERSATION_HISTORY": "",
        "QUESTION": "Question",
        "PROFILE_CONTEXT": "",
    },
    "course_qa": {
        **SHARED_PROMPT_VARIABLES,
        "COURSE_MATERIAL": "Material",
        "CONVERSATION_HISTORY": "",
        "QUESTION": "Question",
        "PROFILE_CONTEXT": "",
    },
    "prompt_generator": {
        **SHARED_PROMPT_VARIABLES,
        "TEXT": "User request",
    },
    "visual_content": {
        **SHARED_PROMPT_VARIABLES,
        "VISUAL_CONTEXT": "Visual elements description",
        "SOURCE_TEXT": "Surrounding course material",
    },
    "ocr_cleanup": {
        **SHARED_PROMPT_VARIABLES,
        "RAW_OCR_TEXT": "Raw noisy OCR text fragment",
    },
    "image_description": {
        **SHARED_PROMPT_VARIABLES,
        "SUGGESTED_TYPE": "diagram",
    },
    "exam_style_question": {
        **SHARED_PROMPT_VARIABLES,
        "TEXT": "Material",
        "QUESTION_COUNT": "5",
        "QUESTION_SCHEMAS": '{"question_type": "multiple_choice"}',
        "REQUESTED_DIFFICULTY": "hard",
        "TOPIC_FOCUS": "All Topics",
        "PROFILE_CONTEXT": "",
    },
}


def test_all_production_templates_render_without_unresolved_placeholders() -> None:
    """Ensure every production template can render cleanly with sample inputs."""
    sample_inputs = SAMPLE_TEMPLATE_INPUTS

    templates = PromptLoader.load_all()
    assert set(sample_inputs) == set(templates)
    for name, sample_vars in sample_inputs.items():
        assert name in templates, f"Template '{name}' missing from prompt catalog"
        rendered = PromptLoader.render(name, sample_vars, allow_deferred=True)
        assert "{{" not in rendered, (
            f"Unresolved placeholder found in rendered '{name}': {rendered}"
        )
        assert "}}" not in rendered, (
            f"Unresolved placeholder found in rendered '{name}': {rendered}"
        )


# ---------------------------------------------------------------------------
# Individual Migrated Prompts Tests
# ---------------------------------------------------------------------------


def test_quiz_template_regression() -> None:
    rendered = PromptLoader.render(
        "quiz",
        {
            **SHARED_PROMPT_VARIABLES,
            "QUESTION_COUNT": "8",
            "QUESTION_TYPES_DIRECTIVE": "Use only these question types: true_false.",
            "QUESTION_SCHEMAS": '{"question_type": "true_false"}',
            "DIFFICULTY_DIRECTIVE": "Every question must be hard.",
            "REQUESTED_DIFFICULTY": "hard",
            "TOPIC_FOCUS": "Eigenvalues",
            "TEXT": "Linear Algebra Lecture",
            "PROFILE_CONTEXT": "",
        },
    )
    assert "Linear Algebra Lecture" in rendered
    assert "{{TEXT}}" not in rendered
    assert "{{QUESTION_COUNT}}" not in rendered
    assert "{{QUESTION_TYPES_DIRECTIVE}}" not in rendered
    assert "{{QUESTION_SCHEMAS}}" not in rendered
    assert "{{DIFFICULTY_DIRECTIVE}}" not in rendered
    assert "{{REQUESTED_DIFFICULTY}}" not in rendered
    assert "{{TOPIC_FOCUS}}" not in rendered
    assert "Generate exactly 8 questions" in rendered
    assert "Use only these question types: true_false." in rendered
    assert "Every question must be hard." in rendered
    assert '{"question_type": "true_false"}' in rendered
    assert 'difficulty field must be exactly "hard"' in rendered
    assert "Eigenvalues" in rendered

    template = PromptLoader.load_template("quiz")
    assert template.output_schema_ref == "QuizGenerationResponse"
    assert template.required_variables == [
        "EDUCATION_LEVEL",
        "COURSE_TITLE",
        "SUBJECT_AREA",
        "MATERIAL_KIND",
        "TEXT",
        "QUESTION_COUNT",
        "QUESTION_TYPES_DIRECTIVE",
        "QUESTION_SCHEMAS",
        "REQUESTED_DIFFICULTY",
        "DIFFICULTY_DIRECTIVE",
        "TOPIC_FOCUS",
        "PROFILE_CONTEXT",
    ]


def test_quiz_grading_template_regression() -> None:
    rendered = PromptLoader.render(
        "quiz_grading",
        {
            **SHARED_PROMPT_VARIABLES,
            "SUBMISSION_COUNT": "2",
            "SUBMISSIONS": "question_number: 1 Question: Why sorted?",
        },
    )
    assert "{{SUBMISSION_COUNT}}" not in rendered
    assert "{{SUBMISSIONS}}" not in rendered
    assert "Why sorted?" in rendered
    assert "2 open-ended submission(s)" in rendered

    template = PromptLoader.load_template("quiz_grading")
    assert template.output_schema_ref == "OpenEndedGradingResponse"
    assert template.required_variables == [
        "EDUCATION_LEVEL",
        "COURSE_TITLE",
        "SUBJECT_AREA",
        "MATERIAL_KIND",
        "SUBMISSION_COUNT",
        "SUBMISSIONS",
    ]


def test_flashcard_template_regression() -> None:
    rendered = PromptLoader.render(
        "flashcard",
        {
            **SHARED_PROMPT_VARIABLES,
            "TEXT": "Data Structures Notes",
            "TOPIC_FOCUS": "Binary Search Trees",
            "PROFILE_CONTEXT": "",
        },
    )
    assert "Data Structures Notes" in rendered
    assert "Binary Search Trees" in rendered
    assert "{{TEXT}}" not in rendered
    assert "{{TOPIC_FOCUS}}" not in rendered
    assert "flashcards" in rendered.lower()

    template = PromptLoader.load_template("flashcard")
    assert template.output_schema_ref == "FlashcardGenerationResponse"
    assert template.required_variables == [
        "EDUCATION_LEVEL",
        "COURSE_TITLE",
        "SUBJECT_AREA",
        "MATERIAL_KIND",
        "TEXT",
        "TOPIC_FOCUS",
        "PROFILE_CONTEXT",
    ]


def test_ai_tutor_template_regression() -> None:
    rendered = PromptLoader.render(
        "ai_tutor",
        {
            **SHARED_PROMPT_VARIABLES,
            "COURSE_MATERIAL": "Operating Systems Virtual Memory",
            "CONVERSATION_HISTORY": (
                "User: What is virtual memory?\n"
                "Assistant: It maps virtual addresses to physical memory."
            ),
            "QUESTION": "What is page fault?",
            "PROFILE_CONTEXT": "",
        },
    )
    assert "Operating Systems Virtual Memory" in rendered
    assert "User: What is virtual memory?" in rendered
    assert "Assistant: It maps virtual addresses to physical memory." in rendered
    assert "What is page fault?" in rendered
    assert "{{COURSE_MATERIAL}}" not in rendered
    assert "{{CONVERSATION_HISTORY}}" not in rendered
    assert "{{QUESTION}}" not in rendered
    assert (
        "open with a targeted hint or a guiding question that points at the "
        "next useful idea" in rendered
    )
    assert "Never open with the bare result." in rendered
    assert "When appropriate, guide the student with a helpful hint" not in rendered
    assert "apparent level" not in rendered
    assert rendered.count("- Adapt the depth, terminology, and style") == 1
    assert "Do not guess the student's level from how they write." in rendered

    template = PromptLoader.load_template("ai_tutor")
    assert template.output_schema_ref == "AiTutorResponse"
    assert template.required_variables == [
        "EDUCATION_LEVEL",
        "COURSE_TITLE",
        "SUBJECT_AREA",
        "MATERIAL_KIND",
        "COURSE_MATERIAL",
        "CONVERSATION_HISTORY",
        "QUESTION",
        "PROFILE_CONTEXT",
    ]


def test_prompt_generator_template_regression() -> None:
    rendered = PromptLoader.render(
        "prompt_generator",
        {
            **SHARED_PROMPT_VARIABLES,
            "TEXT": "Help me write a Python script for web scraping",
        },
    )
    assert "Help me write a Python script for web scraping" in rendered
    assert "{{TEXT}}" not in rendered
    assert "generated_prompt" in rendered

    template = PromptLoader.load_template("prompt_generator")
    assert template.output_schema_ref == "PromptGenerationResponse"
    assert template.required_variables == [
        "EDUCATION_LEVEL",
        "COURSE_TITLE",
        "SUBJECT_AREA",
        "MATERIAL_KIND",
        "TEXT",
    ]


def test_visual_content_template() -> None:
    rendered = PromptLoader.render(
        "visual_content",
        {
            **shared_variables(EducationLevel.UNDERGRADUATE),
            "VISUAL_CONTEXT": "A bar chart showing germination rates by soil type",
            "SOURCE_TEXT": "Chapter 4: Seed Germination",
        },
        allow_deferred=True,
    )
    assert "A bar chart showing germination rates" in rendered
    assert "Chapter 4: Seed Germination" in rendered
    assert "undergraduate" in rendered
    assert EDUCATION_LEVEL_DIRECTIVES[EducationLevel.UNDERGRADUATE] in rendered
    assert "{{" not in rendered

    template = PromptLoader.load_template("visual_content")
    assert template.output_schema_ref == "VisualContentDescriptionResponse"
    assert template.required_variables == [
        "EDUCATION_LEVEL",
        "COURSE_TITLE",
        "SUBJECT_AREA",
        "MATERIAL_KIND",
        "VISUAL_CONTEXT",
        "SOURCE_TEXT",
    ]


def test_ocr_cleanup_template() -> None:
    rendered = PromptLoader.render(
        "ocr_cleanup",
        {
            **SHARED_PROMPT_VARIABLES,
            "RAW_OCR_TEXT": "Defi-nition of algo-rithm: A step-by-step procedvre...",
        },
        allow_deferred=True,
    )
    assert "Defi-nition of algo-rithm" in rendered
    assert "{{RAW_OCR_TEXT}}" not in rendered

    template = PromptLoader.load_template("ocr_cleanup")
    assert template.output_schema_ref == "OcrCleanupResponse"
    assert template.required_variables == [
        "EDUCATION_LEVEL",
        "COURSE_TITLE",
        "SUBJECT_AREA",
        "MATERIAL_KIND",
        "RAW_OCR_TEXT",
    ]


# ---------------------------------------------------------------------------
# Catalog Scope: Active vs Deferred Templates
# ---------------------------------------------------------------------------


DEFERRED_TEMPLATES = {"ocr_cleanup", "exam_style_question", "visual_content"}

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CATALOG_DOC = _REPO_ROOT / "docs" / "prompt_templates.md"
PRODUCTION_PACKAGES = ("services", "routes", "tasks", "workers", "backend", "utils")


def _production_sources() -> dict[str, str]:
    """Every production Python module, keyed by its repo-relative path."""
    sources = {}
    for package in PRODUCTION_PACKAGES:
        for path in sorted((_REPO_ROOT / package).rglob("*.py")):
            sources[path.relative_to(_REPO_ROOT).as_posix()] = path.read_text(
                encoding="utf-8"
            )

    assert sources, "No production sources found to scan"
    return sources


def _is_referenced_as_a_string_literal(name: str, source: str) -> bool:
    return f'"{name}"' in source or f"'{name}'" in source


ACTIVE_TEMPLATE_OWNERS = {
    "study_guide": ("services.study_guide", "StudyGuideService.PROMPT_TEMPLATE_NAME"),
    "quiz": ("services.quiz", "QuizService.PROMPT_TEMPLATE_NAME"),
    "quiz_grading": (
        "services.quiz_grading",
        "QuizGradingService.PROMPT_TEMPLATE_NAME",
    ),
    "flashcard": ("services.flashcard", "FlashcardService.PROMPT_TEMPLATE_NAME"),
    "ai_tutor": ("services.ai_tutor", "AiTutorService.PROMPT_TEMPLATE_NAME"),
    "course_qa": ("services.course_qa", "CourseQAService.PROMPT_TEMPLATE_NAME"),
    "prompt_generator": (
        "services.prompt_generator",
        "PromptGeneratorService.PROMPT_TEMPLATE_NAME",
    ),
    "image_description": (
        "services.image_understanding",
        "_IMAGE_DESCRIPTION_TEMPLATE",
    ),
    "exam_topic_analysis": (
        "services.exam_source_analysis",
        "ExamSourceAnalysisService.PROMPT_TEMPLATE_NAME",
    ),
    "past_exam_question_extraction": (
        "services.exam_question_extraction",
        "PastExamExtractionService.PROMPT_TEMPLATE_NAME",
    ),
    "exam_topic_guide": ("services.exam_topic_study", "GUIDE_TEMPLATE_NAME"),
    "exam_topic_summary": ("services.exam_topic_study", "SUMMARY_TEMPLATE_NAME"),
    "exam_topic_practice": ("services.exam_quiz", "PRACTICE_TEMPLATE_NAME"),
    "exam_topic_exam": ("services.exam_quiz", "EXAM_TEMPLATE_NAME"),
    "exam_similar_questions": ("services.exam_similar_questions", "TEMPLATE_NAME"),
    "exam_mock_exam": ("services.exam_course_artifacts", "MOCK_TEMPLATE_NAME"),
    "exam_review_sheet": (
        "services.exam_course_artifacts",
        "REVIEW_TEMPLATE_NAME",
    ),
}


def _resolve_declared_template_name(module_path: str, attribute_path: str) -> str:
    target = importlib.import_module(module_path)
    for attribute in attribute_path.split("."):
        target = getattr(target, attribute)
    return target


CATALOG_HEADING = "## Production Prompt Catalog"


def _documented_catalog_rows() -> dict[str, tuple[str, str]]:
    """Parse the Status and Owner columns of the catalog table in the docs."""
    rows: dict[str, str] = {}
    inside = False

    for line in _CATALOG_DOC.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            inside = stripped == CATALOG_HEADING
            continue
        if not inside or not stripped.startswith("| `"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        rows[cells[0].strip("`")] = (cells[2].strip("`").lower(), cells[3].strip("`"))

    assert rows, f"No catalog table found under {CATALOG_HEADING!r}"
    return rows


def test_deferred_templates_declare_a_reason() -> None:
    templates = PromptLoader.load_all()
    assert DEFERRED_TEMPLATES <= set(templates)

    for name, template in templates.items():
        if name in DEFERRED_TEMPLATES:
            assert template.status == "deferred", name
            assert template.deferral_reason, (
                f"Deferred template '{name}' must record why it is deferred"
            )
        else:
            assert template.status == "active", name


def test_a_deferred_template_without_a_reason_fails_to_load(tmp_path) -> None:
    (tmp_path / "unexplained.json").write_text(
        json.dumps(
            {
                "name": "unexplained",
                "version": "1.0.0",
                "status": "deferred",
                "template": "Body with no placeholders.",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(PromptTemplateValidationError):
        PromptLoader.load_template("unexplained", directory=tmp_path, reload=True)


def test_rendering_a_deferred_template_is_refused() -> None:
    for name in sorted(DEFERRED_TEMPLATES):
        sample_vars = SAMPLE_TEMPLATE_INPUTS[name]

        with pytest.raises(PromptTemplateDeferredError):
            PromptLoader.render(name, sample_vars)

        rendered = PromptLoader.render(name, sample_vars, allow_deferred=True)
        assert "{{" not in rendered


def test_no_production_module_references_a_deferred_template() -> None:
    for file_path, source in _production_sources().items():
        for name in sorted(DEFERRED_TEMPLATES):
            assert not _is_referenced_as_a_string_literal(name, source), (
                f"{file_path} references deferred template '{name}'; "
                "a deferred template must not be wired into production behavior"
            )


def test_every_template_is_either_owned_or_deferred() -> None:
    """A template cannot sit in the catalog unclassified."""
    classified = set(ACTIVE_TEMPLATE_OWNERS) | DEFERRED_TEMPLATES

    assert set(PromptLoader.load_all()) == classified, (
        "Every template must either name an owning service in "
        "ACTIVE_TEMPLATE_OWNERS or be listed in DEFERRED_TEMPLATES"
    )
    assert not (set(ACTIVE_TEMPLATE_OWNERS) & DEFERRED_TEMPLATES)


def test_each_active_template_is_named_by_its_owning_service() -> None:
    templates = PromptLoader.load_all()

    for name, (module_path, attribute_path) in sorted(ACTIVE_TEMPLATE_OWNERS.items()):
        assert templates[name].status == "active", name
        assert _resolve_declared_template_name(module_path, attribute_path) == name, (
            f"{module_path}.{attribute_path} no longer resolves to the '{name}' "
            "template. An active template must be rendered by its owning service."
        )


def test_catalog_documentation_matches_template_status() -> None:
    documented = _documented_catalog_rows()
    templates = PromptLoader.load_all()

    assert set(documented) == set(templates), (
        "docs/prompt_templates.md catalog table must list exactly the templates "
        "in app/prompts/"
    )
    for name, template in templates.items():
        documented_status, documented_owner = documented[name]
        assert documented_status == template.status, (
            f"Catalog documentation records '{name}' as {documented_status}, "
            f"but the template declares {template.status}"
        )
        if name in ACTIVE_TEMPLATE_OWNERS:
            module_path, _ = ACTIVE_TEMPLATE_OWNERS[name]
            expected_owner = module_path.replace(".", "/") + ".py"
            assert documented_owner == expected_owner, (
                f"Catalog documentation names '{documented_owner}' as the owner of "
                f"'{name}', but the template is rendered by {expected_owner}"
            )


# ---------------------------------------------------------------------------
# Tutor Policy: Hint-First Consistency
# ---------------------------------------------------------------------------


HINT_FIRST_TUTOR_MARKERS = (
    "open with a targeted hint or a guiding question",
    "never open with the bare result",
    "if the student is still stuck, escalate one step at a time",
    "explain the relevant concept or method",
    "walk through the key intermediate steps",
    "break complex reasoning into manageable steps",
    "for a conceptual question",
    "do not withhold a definition behind a counter-question",
)

CONTRADICTORY_TUTOR_INSTRUCTIONS = (
    "answer the student's question directly",
    "provide the direct answer",
    "give the answer immediately",
    "always provide direct answers",
    "answer directly",
    "when appropriate, guide the student with a helpful hint",
)


def _rendered_ai_tutor() -> str:
    return PromptLoader.render("ai_tutor", SAMPLE_TEMPLATE_INPUTS["ai_tutor"])


def test_ai_tutor_is_consistently_hint_first() -> None:
    lowered = _rendered_ai_tutor().lower()

    for marker in HINT_FIRST_TUTOR_MARKERS:
        assert marker in lowered, f"ai_tutor prompt is missing: {marker!r}"


def test_ai_tutor_rejects_contradictory_tutoring_instructions() -> None:
    template = PromptLoader.load_template("ai_tutor")
    metadata = " ".join(template.style_constraints + template.safety_constraints)
    surfaces = {
        "rendered prompt": _rendered_ai_tutor().lower(),
        "declared constraints": metadata.lower(),
    }

    for surface, text in surfaces.items():
        for instruction in CONTRADICTORY_TUTOR_INSTRUCTIONS:
            assert instruction not in text, (
                f"ai_tutor {surface} contains a direct-answer instruction "
                f"that contradicts hint-first tutoring: {instruction!r}"
            )


@pytest.mark.parametrize(
    "level",
    [EducationLevel.HIGH_SCHOOL, EducationLevel.GRADUATE],
)
def test_tutoring_method_is_identical_across_education_levels(
    level: EducationLevel,
) -> None:
    rendered = PromptLoader.render(
        "ai_tutor",
        {
            **shared_variables(level),
            "COURSE_MATERIAL": "Material",
            "CONVERSATION_HISTORY": "",
            "QUESTION": "How do I solve this?",
            "PROFILE_CONTEXT": "",
        },
    )
    lowered = rendered.lower()

    assert EDUCATION_LEVEL_DIRECTIVES[level] in rendered
    for marker in HINT_FIRST_TUTOR_MARKERS:
        assert marker in lowered, (level.value, marker)
    for instruction in CONTRADICTORY_TUTOR_INSTRUCTIONS:
        assert instruction not in lowered, (level.value, instruction)
    assert (
        "the education level changes how you explain, never whether you guide"
        in lowered
    )


def test_course_qa_remains_direct_answer() -> None:
    """Hint-first tutoring must not leak into the separate question-answering mode."""
    rendered = PromptLoader.render("course_qa", SAMPLE_TEMPLATE_INPUTS["course_qa"])
    lowered = rendered.lower()

    assert "answer the question directly" in lowered
    for marker in HINT_FIRST_TUTOR_MARKERS:
        assert marker not in lowered, (
            f"course_qa adopted a tutoring instruction it must not carry: {marker!r}"
        )
