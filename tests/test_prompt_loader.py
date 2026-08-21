# tests/test_prompt_loader.py
"""Deterministic unit and regression tests for Lumina's Learner-Aware Prompt Library.

Covers:
- Template loading, parsing, and strict metadata validation
- LearnerContext validation, safe defaults, and directive rendering
- Education level prompt adaptation (high_school, undergraduate, graduate, unspecified, other)
- Comprehensive regression preventing hardcoded university or domain assumptions
- Deterministic rendering of all production templates with zero unresolved placeholders
- Observability and privacy-safe render metadata generation
"""

import json
import pytest
from pydantic import ValidationError

from schemas.learner_context import (
    EducationLevel,
    LearnerContext,
)
from schemas.prompt_template import (
    MissingPromptVariableError,
    PromptTemplateModel,
    PromptTemplateNotFoundError,
    PromptTemplateSyntaxError,
    PromptTemplateValidationError,
    UnexpectedPromptVariableError,
)
from services.prompt_components import (
    build_grounding_block,
    build_learner_context_block,
    build_safety_block,
)
from services.prompt_loader import PromptLoader


# ---------------------------------------------------------------------------
# Parser and Loader Unit Tests
# ---------------------------------------------------------------------------


def test_load_valid_study_guide_template() -> None:
    template = PromptLoader.load_template("study_guide", reload=True)
    assert template.name == "study_guide"
    assert template.version == "2.0.0"
    assert template.required_variables == [
        "TEXT",
        "SUMMARY_FORMAT",
        "TOPIC_FOCUS",
        "SUMMARY_LENGTH",
        "DETAIL_LEVEL",
        "SUMMARY_MODE",
    ]
    assert "LEARNER_CONTEXT" in template.optional_variables
    assert template.output_schema_ref == "StudyGuideResponse"
    assert len(template.style_constraints) > 0
    assert len(template.safety_constraints) > 0
    assert "{{TEXT}}" in template.template
    assert "{{SUMMARY_FORMAT}}" in template.template
    assert "{{TOPIC_FOCUS}}" in template.template
    assert "{{LEARNER_CONTEXT}}" in template.template


def test_load_all_built_in_templates() -> None:
    templates = PromptLoader.load_all()
    expected_names = {
        "study_guide",
        "quiz",
        "quiz_grading",
        "flashcard",
        "ai_tutor",
        "course_qa",
        "prompt_generator",
        "visual_content",
        "ocr_cleanup",
    }
    assert expected_names.issubset(set(templates.keys()))
    for name in expected_names:
        template = templates[name]
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
# LearnerContext Unit & Validation Tests
# ---------------------------------------------------------------------------


def test_learner_context_safe_default() -> None:
    context = LearnerContext()
    assert context.education_level == EducationLevel.UNSPECIFIED
    assert context.course_name is None
    assert context.current_topic is None
    assert context.difficulty_level is None

    directive = context.render_directive()
    assert "General Learner (Unspecified Level)" in directive
    assert "without assuming a specific academic tier" in directive
    # Ensure no university identity is assumed in the default
    assert "university" not in directive.lower()


@pytest.mark.parametrize(
    ("level", "expected_phrase"),
    [
        (EducationLevel.HIGH_SCHOOL, "High-School Level"),
        (EducationLevel.UNDERGRADUATE, "Undergraduate Level"),
        (EducationLevel.GRADUATE, "Graduate / Advanced Level"),
        (EducationLevel.UNSPECIFIED, "General Learner (Unspecified Level)"),
        (EducationLevel.OTHER, "General Learner"),
    ],
)
def test_learner_context_education_level_directives(
    level: EducationLevel, expected_phrase: str
) -> None:
    context = LearnerContext(education_level=level)
    directive = context.render_directive()
    assert expected_phrase in directive


def test_learner_context_with_metadata_fields() -> None:
    context = LearnerContext(
        education_level=EducationLevel.HIGH_SCHOOL,
        course_name="AP Biology",
        current_topic="Cellular Respiration",
        difficulty_level="introductory",
        study_objective="exam_preparation",
        detail_level="step_by_step",
        language="English",
    )
    rendered = context.render_directive()
    assert "High-School Level" in rendered
    assert "Course/Subject: AP Biology" in rendered
    assert "Current Topic: Cellular Respiration" in rendered
    assert "Target Difficulty: introductory" in rendered
    assert "Study Objective: exam_preparation" in rendered
    assert "Preferred Detail: step_by_step" in rendered
    assert "Preferred Language: English" in rendered


def test_learner_context_rejects_invalid_education_level() -> None:
    with pytest.raises(ValidationError):
        LearnerContext(education_level="kindergarten")  # type: ignore[arg-type]


def test_learner_context_rejects_nul_bytes() -> None:
    with pytest.raises(ValidationError):
        LearnerContext(course_name="Biology\x00101")


def test_learner_context_telemetry_dict() -> None:
    context = LearnerContext(
        education_level=EducationLevel.GRADUATE,
        course_name="Distributed Systems",
    )
    metadata = context.to_metadata_dict()
    assert metadata["education_level"] == "graduate"
    assert metadata["has_course_name"] is True
    assert metadata["has_topic"] is False
    # Ensure private course name string itself is NOT in metadata dictionary
    assert "Distributed Systems" not in metadata.values()


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

    lc_block = build_learner_context_block(
        LearnerContext(education_level=EducationLevel.UNDERGRADUATE)
    )
    assert "Undergraduate Level" in lc_block


def test_prompt_components_build_learner_context_from_dict() -> None:
    lc_block = build_learner_context_block(
        {"education_level": "high_school", "course_name": "Physics"}
    )
    assert "High-School Level" in lc_block
    assert "Course/Subject: Physics" in lc_block


def test_prompt_components_build_learner_context_invalid_type() -> None:
    with pytest.raises(ValueError) as exc_info:
        build_learner_context_block(12345)  # type: ignore[arg-type]
    assert "Expected LearnerContext, dict, or None" in str(exc_info.value)


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
    assert "{{LEARNER_CONTEXT}}" not in rendered
    assert "Sample Lecture Notes Content" in rendered
    assert "Requested summary format: overview." in rendered
    assert "Working Memory" in rendered
    assert "General Learner (Unspecified Level)" in rendered


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

    rendered = custom.render(
        {"REQUIRED_TEXT": "Do something", "OPTIONAL_HINT": "Be quick"}
    )
    assert "Instruction: Do something (Hint: Be quick)" == rendered

    with pytest.raises(UnexpectedPromptVariableError):
        custom.render({"REQUIRED_TEXT": "Do something", "UNKNOWN": "value"})

    with pytest.raises(MissingPromptVariableError):
        custom.render({"OPTIONAL_HINT": "only hint"})


# ---------------------------------------------------------------------------
# Education Level Adaptation Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "level",
    [
        EducationLevel.HIGH_SCHOOL,
        EducationLevel.UNDERGRADUATE,
        EducationLevel.GRADUATE,
        EducationLevel.UNSPECIFIED,
    ],
)
def test_study_guide_adaptation_across_education_levels(level: EducationLevel) -> None:
    context = LearnerContext(education_level=level, course_name="Chemistry")
    rendered = PromptLoader.render(
        "study_guide",
        {
            "TEXT": "Thermodynamics principles...",
            "SUMMARY_FORMAT": "Requested summary format: comprehensive.",
            "TOPIC_FOCUS": "Entropy",
            "SUMMARY_LENGTH": "Between 400 and 600 words.",
            "DETAIL_LEVEL": "Requested detail level: detailed.",
            "SUMMARY_MODE": "Requested summary mode: general.",
        },
        learner_context=context,
    )
    assert "{{" not in rendered
    assert "}}" not in rendered
    assert "Course/Subject: Chemistry" in rendered
    if level == EducationLevel.HIGH_SCHOOL:
        assert "High-School Level" in rendered
        assert "Prioritize foundational clarity" in rendered
    elif level == EducationLevel.UNDERGRADUATE:
        assert "Undergraduate Level" in rendered
        assert "Maintain standard academic rigor" in rendered
    elif level == EducationLevel.GRADUATE:
        assert "Graduate / Advanced Level" in rendered
        assert (
            "Provide comprehensive, in-depth, and rigorous academic analysis"
            in rendered
        )
    elif level == EducationLevel.UNSPECIFIED:
        assert "General Learner (Unspecified Level)" in rendered


def test_quiz_adaptation_with_learner_context() -> None:
    context = LearnerContext(
        education_level=EducationLevel.HIGH_SCHOOL,
        current_topic="Newtonian Mechanics",
    )
    rendered = PromptLoader.render(
        "quiz",
        {
            "QUESTION_COUNT": "5",
            "QUESTION_TYPES_DIRECTIVE": "Use only multiple_choice.",
            "QUESTION_SCHEMAS": '{"question_type": "multiple_choice"}',
            "DIFFICULTY_DIRECTIVE": "Standard high school physics level.",
            "REQUESTED_DIFFICULTY": "medium",
            "TOPIC_FOCUS": "Newton's First Law",
            "TEXT": "Physics Lecture Notes...",
        },
        learner_context=context,
    )
    assert "High-School Level" in rendered
    assert "Current Topic: Newtonian Mechanics" in rendered
    assert "Generate exactly 5 questions" in rendered
    assert "{{" not in rendered


def test_ai_tutor_adaptation_with_learner_context() -> None:
    context = LearnerContext(education_level=EducationLevel.GRADUATE)
    rendered = PromptLoader.render(
        "ai_tutor",
        {
            "COURSE_MATERIAL": "Advanced Operating Systems",
            "CONVERSATION_HISTORY": "User: What is RCU?\nAssistant: Read-Copy Update.",
            "QUESTION": "How does grace period detection work in preemptible RCU?",
        },
        learner_context=context,
    )
    assert "Graduate / Advanced Level" in rendered
    assert "Advanced Operating Systems" in rendered
    assert "grace period detection" in rendered
    assert "{{" not in rendered


def test_course_qa_adaptation_with_learner_context() -> None:
    context = LearnerContext(education_level=EducationLevel.UNDERGRADUATE)
    rendered = PromptLoader.render(
        "course_qa",
        {
            "COURSE_MATERIAL": "Computer Networks Notes",
            "CONVERSATION_HISTORY": "",
            "QUESTION": "What is the difference between TCP and UDP?",
        },
        learner_context=context,
    )
    assert "Undergraduate Level" in rendered
    assert "Computer Networks Notes" in rendered
    assert "{{" not in rendered


# ---------------------------------------------------------------------------
# Observability & Metadata Tests
# ---------------------------------------------------------------------------


def test_get_render_metadata_returns_safe_telemetry() -> None:
    context = LearnerContext(
        education_level=EducationLevel.UNDERGRADUATE,
        course_name="Linear Algebra",
        current_topic="Eigenvalues",
    )
    meta = PromptLoader.get_render_metadata(
        "study_guide",
        {
            "TEXT": "Sensitive raw lecture text",
            "SUMMARY_FORMAT": "format",
            "TOPIC_FOCUS": "focus",
            "SUMMARY_LENGTH": "length",
            "DETAIL_LEVEL": "detail",
            "SUMMARY_MODE": "mode",
        },
        learner_context=context,
    )
    assert meta["template_name"] == "study_guide"
    assert meta["template_version"] == "2.0.0"
    assert meta["output_schema_ref"] == "StudyGuideResponse"
    assert meta["education_level"] == "undergraduate"
    assert meta["learner_context_applied"] is True
    assert meta["learner_metadata"]["has_course_name"] is True
    assert meta["learner_metadata"]["has_topic"] is True

    # Assert raw sensitive contents are NOT leaked into metadata
    meta_str = json.dumps(meta)
    assert "Sensitive raw lecture text" not in meta_str
    assert "Linear Algebra" not in meta_str
    assert "Eigenvalues" not in meta_str


# ---------------------------------------------------------------------------
# Strict Anti-Assumption & Anti-Hallucination Regression Tests
# ---------------------------------------------------------------------------


def test_no_production_prompt_contains_hardcoded_university_assumptions() -> None:
    """Strict regression test ensuring no template hardcodes university assumptions."""
    templates = PromptLoader.load_all()
    forbidden_phrases = [
        "university assistant",
        "university student",
        "university students",
        "university teaching assistant",
        "university instructor",
        "average university student",
        "Computer Science teaching assistant",
    ]

    for name, template in templates.items():
        template_text = template.template.lower()
        description = (template.description or "").lower()

        for phrase in forbidden_phrases:
            assert phrase.lower() not in template_text, (
                f"Template '{name}' contains forbidden hardcoded phrase: '{phrase}'"
            )
            assert phrase.lower() not in description, (
                f"Template description for '{name}' contains forbidden phrase: '{phrase}'"
            )


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


def test_all_production_templates_render_without_unresolved_placeholders() -> None:
    """Ensure every production template can render cleanly with sample inputs."""
    sample_inputs = {
        "study_guide": {
            "TEXT": "Lecture text",
            "SUMMARY_FORMAT": "Format directive",
            "TOPIC_FOCUS": "All Topics",
            "SUMMARY_LENGTH": "Medium",
            "DETAIL_LEVEL": "Standard",
            "SUMMARY_MODE": "General",
        },
        "quiz": {
            "TEXT": "Lecture text",
            "QUESTION_COUNT": "5",
            "QUESTION_TYPES_DIRECTIVE": "Types",
            "QUESTION_SCHEMAS": "Schemas",
            "REQUESTED_DIFFICULTY": "medium",
            "DIFFICULTY_DIRECTIVE": "Difficulty directive",
            "TOPIC_FOCUS": "All Topics",
        },
        "quiz_grading": {
            "SUBMISSION_COUNT": "1",
            "SUBMISSIONS": "Submissions text",
        },
        "flashcard": {
            "TEXT": "Lecture text",
        },
        "ai_tutor": {
            "COURSE_MATERIAL": "Material",
            "CONVERSATION_HISTORY": "",
            "QUESTION": "Question",
        },
        "course_qa": {
            "COURSE_MATERIAL": "Material",
            "CONVERSATION_HISTORY": "",
            "QUESTION": "Question",
        },
        "prompt_generator": {
            "TEXT": "User request",
        },
        "visual_content": {
            "VISUAL_CONTEXT": "Visual elements description",
            "SOURCE_TEXT": "Surrounding lecture material",
        },
        "ocr_cleanup": {
            "RAW_OCR_TEXT": "Raw noisy OCR text fragment",
        },
    }

    templates = PromptLoader.load_all()
    for name, sample_vars in sample_inputs.items():
        assert name in templates, f"Template '{name}' missing from prompt catalog"
        rendered = PromptLoader.render(name, sample_vars)
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
            "QUESTION_COUNT": "8",
            "QUESTION_TYPES_DIRECTIVE": "Use only these question types: true_false.",
            "QUESTION_SCHEMAS": '{"question_type": "true_false"}',
            "DIFFICULTY_DIRECTIVE": "Every question must be hard.",
            "REQUESTED_DIFFICULTY": "hard",
            "TOPIC_FOCUS": "Eigenvalues",
            "TEXT": "Linear Algebra Lecture",
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
    assert "{{LEARNER_CONTEXT}}" not in rendered
    assert "Generate exactly 8 questions" in rendered
    assert "Use only these question types: true_false." in rendered
    assert "Every question must be hard." in rendered
    assert '{"question_type": "true_false"}' in rendered
    assert 'difficulty field must be exactly "hard"' in rendered
    assert "Eigenvalues" in rendered

    template = PromptLoader.load_template("quiz")
    assert template.output_schema_ref == "QuizGenerationResponse"
    assert template.required_variables == [
        "TEXT",
        "QUESTION_COUNT",
        "QUESTION_TYPES_DIRECTIVE",
        "QUESTION_SCHEMAS",
        "REQUESTED_DIFFICULTY",
        "DIFFICULTY_DIRECTIVE",
        "TOPIC_FOCUS",
    ]


def test_quiz_grading_template_regression() -> None:
    rendered = PromptLoader.render(
        "quiz_grading",
        {
            "SUBMISSION_COUNT": "2",
            "SUBMISSIONS": "question_number: 1 Question: Why sorted?",
        },
    )
    assert "{{SUBMISSION_COUNT}}" not in rendered
    assert "{{SUBMISSIONS}}" not in rendered
    assert "Why sorted?" in rendered
    assert "2 open-ended question(s)" in rendered

    template = PromptLoader.load_template("quiz_grading")
    assert template.output_schema_ref == "OpenEndedGradingResponse"
    assert template.required_variables == ["SUBMISSION_COUNT", "SUBMISSIONS"]


def test_flashcard_template_regression() -> None:
    rendered = PromptLoader.render(
        "flashcard",
        {"TEXT": "Data Structures Notes"},
    )
    assert "Data Structures Notes" in rendered
    assert "{{TEXT}}" not in rendered
    assert "{{LEARNER_CONTEXT}}" not in rendered
    assert "flashcards" in rendered.lower()

    template = PromptLoader.load_template("flashcard")
    assert template.output_schema_ref == "FlashcardGenerationResponse"
    assert template.required_variables == ["TEXT"]


def test_ai_tutor_template_regression() -> None:
    rendered = PromptLoader.render(
        "ai_tutor",
        {
            "COURSE_MATERIAL": "Operating Systems Virtual Memory",
            "CONVERSATION_HISTORY": (
                "User: What is virtual memory?\n"
                "Assistant: It maps virtual addresses to physical memory."
            ),
            "QUESTION": "What is page fault?",
        },
    )
    assert "Operating Systems Virtual Memory" in rendered
    assert "User: What is virtual memory?" in rendered
    assert "Assistant: It maps virtual addresses to physical memory." in rendered
    assert "What is page fault?" in rendered
    assert "{{COURSE_MATERIAL}}" not in rendered
    assert "{{CONVERSATION_HISTORY}}" not in rendered
    assert "{{QUESTION}}" not in rendered
    assert "{{LEARNER_CONTEXT}}" not in rendered
    assert (
        "Begin with a concise helpful hint or guiding question before giving the full "
        "explanation." in rendered
    )

    template = PromptLoader.load_template("ai_tutor")
    assert template.output_schema_ref == "AiTutorResponse"
    assert template.required_variables == [
        "COURSE_MATERIAL",
        "CONVERSATION_HISTORY",
        "QUESTION",
    ]


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


def test_visual_content_template() -> None:
    rendered = PromptLoader.render(
        "visual_content",
        {
            "VISUAL_CONTEXT": "A bar chart showing execution times of Sorting Algorithms",
            "SOURCE_TEXT": "Chapter 4: Sorting and Searching Algorithms",
        },
        learner_context=LearnerContext(education_level=EducationLevel.UNDERGRADUATE),
    )
    assert "A bar chart showing execution times" in rendered
    assert "Chapter 4: Sorting and Searching Algorithms" in rendered
    assert "Undergraduate Level" in rendered
    assert "{{VISUAL_CONTEXT}}" not in rendered
    assert "{{SOURCE_TEXT}}" not in rendered
    assert "{{LEARNER_CONTEXT}}" not in rendered

    template = PromptLoader.load_template("visual_content")
    assert template.output_schema_ref == "VisualContentDescriptionResponse"
    assert template.required_variables == ["VISUAL_CONTEXT", "SOURCE_TEXT"]


def test_ocr_cleanup_template() -> None:
    rendered = PromptLoader.render(
        "ocr_cleanup",
        {
            "RAW_OCR_TEXT": "Defi-nition of algo-rithm: A step-by-step procedvre...",
        },
    )
    assert "Defi-nition of algo-rithm" in rendered
    assert "{{RAW_OCR_TEXT}}" not in rendered

    template = PromptLoader.load_template("ocr_cleanup")
    assert template.output_schema_ref == "OcrCleanupResponse"
    assert template.required_variables == ["RAW_OCR_TEXT"]
