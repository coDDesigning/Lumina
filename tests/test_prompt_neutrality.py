from __future__ import annotations

import re
from pathlib import Path

import pytest

from prompt_neutrality_policy import (
    DISCIPLINE_VOCABULARY,
    LEVEL_VOCABULARY,
    assert_no_level_or_discipline_fallback,
    assert_source_neutral,
    find_neutrality_violations,
    normalize,
)
from schemas.prompt_context import (
    EDUCATION_LEVEL_DIRECTIVES,
    MATERIAL_KIND_DIRECTIVES,
    EducationLevel,
    MaterialKind,
    PromptContext,
)
from services.profile_knowledge import (
    ProfileKnowledgeContext,
    format_profile_context,
)
from services.prompt_loader import PromptLoader

REPO_ROOT = Path(__file__).resolve().parents[1]

PRODUCTION_PACKAGES = (
    "services",
    "routes",
    "tasks",
    "workers",
    "backend",
    "utils",
    "schemas",
)

SHARED_VARIABLES = frozenset(
    {"EDUCATION_LEVEL", "COURSE_TITLE", "SUBJECT_AREA", "MATERIAL_KIND"}
)

PROFILE_CONTEXT_FIXTURE = format_profile_context(
    ProfileKnowledgeContext(
        text="I learn best from worked examples and short recaps.",
        items_used=1,
        items_available=1,
        truncated=False,
    )
)

MODEL_FACING_TEXT_FIELDS = ("template", "system_instruction", "description")
MODEL_FACING_LIST_FIELDS = ("style_constraints", "safety_constraints")

ALL_TEMPLATE_NAMES = sorted(PromptLoader.load_all())


def directive_texts() -> list[str]:
    return list(EDUCATION_LEVEL_DIRECTIVES.values()) + list(
        MATERIAL_KIND_DIRECTIVES.values()
    )


def directive_exemptions() -> list[str]:
    return [normalize(text) for text in directive_texts()]


def production_sources() -> dict[str, str]:
    sources: dict[str, str] = {}
    for package in PRODUCTION_PACKAGES:
        for path in sorted((REPO_ROOT / package).rglob("*.py")):
            sources[path.relative_to(REPO_ROOT).as_posix()] = path.read_text(
                encoding="utf-8"
            )

    assert sources, "No production sources found to scan"
    return sources


def template_surfaces(name: str) -> dict[str, str]:
    template = PromptLoader.load_template(name)
    surfaces = {
        field: getattr(template, field) or "" for field in MODEL_FACING_TEXT_FIELDS
    }
    for field in MODEL_FACING_LIST_FIELDS:
        surfaces[field] = "\n".join(getattr(template, field))
    return surfaces


# ---------------------------------------------------------------------------
# Layer 1: source neutrality
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ALL_TEMPLATE_NAMES)
def test_no_production_template_hardcodes_a_level_or_discipline(name: str) -> None:
    for field, text in template_surfaces(name).items():
        assert_source_neutral(text, f"app/prompts/{name}.json ({field})")


def test_no_production_module_carries_an_inline_prompt_assumption() -> None:
    exemptions = directive_exemptions()

    for file_path, source in production_sources().items():
        assert_source_neutral(source, file_path, exempt=exemptions, with_lines=True)


def test_the_carve_out_only_covers_declared_directive_values() -> None:
    declared = {normalize(text) for text in directive_texts()}

    assert set(directive_exemptions()) == declared, (
        "The inline-source scan may only exempt the education-level and "
        "material-kind directive values, which are checked by their own "
        "stricter tests. Nothing else may bypass the scanner."
    )


# ---------------------------------------------------------------------------
# Layer 1b: the directive maps that carry the real prompt prose
# ---------------------------------------------------------------------------


LEVEL_OWN_VOCABULARY = {
    EducationLevel.HIGH_SCHOOL: (r"high[-\s]school", r"secondary[-\s]school"),
    EducationLevel.UNDERGRADUATE: ("undergraduate", "college", "bachelor"),
    EducationLevel.GRADUATE: ("graduate", "master", "doctoral", "phd"),
    EducationLevel.PROFESSIONAL_OTHER: ("professional", r"independent\s+learner"),
    EducationLevel.UNSPECIFIED: (),
}

MATERIAL_OWN_VOCABULARY = {
    MaterialKind.LECTURE_NOTES: (r"lecture\s+notes?",),
    MaterialKind.SLIDES: ("slides?",),
    MaterialKind.TEXTBOOK: ("textbooks?",),
    MaterialKind.SYLLABUS: ("syllabus",),
    MaterialKind.ASSIGNMENT: ("assignments?", r"problem[-\s]sets?"),
    MaterialKind.PAST_EXAM: ("exams?", "examinations?"),
    MaterialKind.ARTICLE: ("articles?", "papers?"),
    MaterialKind.NOTES: (r"study\s+notes?",),
    MaterialKind.OTHER: (),
    MaterialKind.MIXED: (),
    MaterialKind.UNSPECIFIED: (),
}


def names_vocabulary(text: str, word: str) -> re.Match[str] | None:
    return re.search(rf"(?:{word})", text, re.IGNORECASE)


def test_every_level_and_material_kind_is_covered_by_a_directive() -> None:
    assert set(EDUCATION_LEVEL_DIRECTIVES) == set(EducationLevel)
    assert set(MATERIAL_KIND_DIRECTIVES) == set(MaterialKind)
    assert set(LEVEL_OWN_VOCABULARY) == set(EducationLevel)
    assert set(MATERIAL_OWN_VOCABULARY) == set(MaterialKind)


@pytest.mark.parametrize("level", sorted(EducationLevel, key=lambda item: item.value))
def test_a_level_directive_never_names_another_level(level: EducationLevel) -> None:
    directive = EDUCATION_LEVEL_DIRECTIVES[level]

    for other, vocabulary in LEVEL_OWN_VOCABULARY.items():
        if other is level:
            continue
        for word in vocabulary:
            assert names_vocabulary(directive, word) is None, (
                f"The {level.value} directive names {other.value} vocabulary "
                f"({word!r}). Each directive may describe only its own level."
            )


def test_the_unspecified_level_directive_names_no_academic_tier() -> None:
    directive = EDUCATION_LEVEL_DIRECTIVES[EducationLevel.UNSPECIFIED]
    match = LEVEL_VOCABULARY.search(directive)

    named = match.group(0) if match else ""
    assert match is None, (
        f"The unspecified education-level directive names {named!r}. An "
        "unknown level must not silently resolve to an academic tier."
    )


@pytest.mark.parametrize("directive", sorted(directive_texts()))
def test_no_shared_directive_names_a_discipline(directive: str) -> None:
    match = DISCIPLINE_VOCABULARY.search(directive)

    named = match.group(0) if match else ""
    assert match is None, (
        f"A shared directive names the discipline {named!r}: {directive!r}. "
        "Discipline must come from SUBJECT_AREA."
    )


@pytest.mark.parametrize("kind", sorted(MaterialKind, key=lambda item: item.value))
def test_a_material_directive_never_names_another_kind(kind: MaterialKind) -> None:
    directive = MATERIAL_KIND_DIRECTIVES[kind]

    for other, vocabulary in MATERIAL_OWN_VOCABULARY.items():
        if other is kind:
            continue
        for word in vocabulary:
            assert names_vocabulary(directive, word) is None, (
                f"The {kind.value} directive names {other.value} vocabulary "
                f"({word!r}). No prompt may assume every source is one kind."
            )


# ---------------------------------------------------------------------------
# Layer 2: representative rendering matrix
# ---------------------------------------------------------------------------


TEMPLATE_EXTRA_VARIABLES: dict[str, dict[str, str]] = {
    "study_guide": {
        "PROFILE_CONTEXT": PROFILE_CONTEXT_FIXTURE,
        "TEXT": "Course material body",
        "SUMMARY_FORMAT": "Requested summary format: comprehensive.",
        "TOPIC_FOCUS": "All Topics",
        "SUMMARY_LENGTH": "Between 400 and 600 words.",
        "DETAIL_LEVEL": "Requested detail level: detailed.",
        "SUMMARY_MODE": "Requested summary mode: general.",
    },
    "quiz": {
        "PROFILE_CONTEXT": PROFILE_CONTEXT_FIXTURE,
        "TEXT": "Course material body",
        "QUESTION_COUNT": "5",
        "QUESTION_TYPES_DIRECTIVE": "Use only multiple_choice.",
        "QUESTION_SCHEMAS": '{"question_type": "multiple_choice"}',
        "REQUESTED_DIFFICULTY": "medium",
        "DIFFICULTY_DIRECTIVE": "Pitch questions at the stated context.",
        "TOPIC_FOCUS": "All Topics",
    },
    "quiz_grading": {
        "SUBMISSION_COUNT": "1",
        "SUBMISSIONS": "Submitted answers body",
    },
    "exam_topic_analysis": {
        "TOPIC_FOCUS": "All Topics",
        "DECLARED_TOPICS": "Declared topics body",
        "SYLLABUS_TEXT": "Syllabus body",
        "TEXT": "Course material body",
    },
    "past_exam_question_extraction": {
        "TEXT": "Examination paper body",
    },
    "flashcard": {
        "PROFILE_CONTEXT": PROFILE_CONTEXT_FIXTURE,
        "TEXT": "Course material body",
        "TOPIC_FOCUS": "All Topics",
    },
    "ai_tutor": {
        "PROFILE_CONTEXT": PROFILE_CONTEXT_FIXTURE,
        "COURSE_MATERIAL": "Course material body",
        "CONVERSATION_HISTORY": "",
        "QUESTION": "How do I approach this exercise?",
    },
    "course_qa": {
        "PROFILE_CONTEXT": PROFILE_CONTEXT_FIXTURE,
        "COURSE_MATERIAL": "Course material body",
        "CONVERSATION_HISTORY": "",
        "QUESTION": "What does this term mean?",
    },
    "prompt_generator": {"TEXT": "Turn this into a better prompt"},
    "image_description": {"SUGGESTED_TYPE": "diagram"},
    "exam_style_question": {
        "PROFILE_CONTEXT": PROFILE_CONTEXT_FIXTURE,
        "TEXT": "Course material body",
        "QUESTION_COUNT": "5",
        "QUESTION_SCHEMAS": '{"question_type": "multiple_choice"}',
        "REQUESTED_DIFFICULTY": "hard",
        "TOPIC_FOCUS": "All Topics",
    },
    "visual_content": {
        "VISUAL_CONTEXT": "A labelled diagram",
        "SOURCE_TEXT": "Surrounding course material",
    },
    "ocr_cleanup": {"RAW_OCR_TEXT": "Raw noisy OCR text fragment"},
}

UNSPECIFIED_SCENARIO = "unspecified"
COMPUTER_SCIENCE_SCENARIO = "undergraduate_computer_science"

RENDER_SCENARIOS: dict[str, PromptContext] = {
    "high_school_biology": PromptContext(
        education_level=EducationLevel.HIGH_SCHOOL,
        course_title="AP Biology",
        subject_area="Biology",
        material_kind=MaterialKind.TEXTBOOK,
    ),
    "undergraduate_psychology": PromptContext(
        education_level=EducationLevel.UNDERGRADUATE,
        course_title="Introduction to Psychology",
        subject_area="Psychology",
        material_kind=MaterialKind.SLIDES,
    ),
    "graduate_economics": PromptContext(
        education_level=EducationLevel.GRADUATE,
        course_title="Advanced Econometrics",
        subject_area="Economics",
        material_kind=MaterialKind.ARTICLE,
    ),
    "professional_other_nursing": PromptContext(
        education_level=EducationLevel.PROFESSIONAL_OTHER,
        course_title="Clinical Pharmacology Refresher",
        subject_area="Nursing",
        material_kind=MaterialKind.NOTES,
    ),
    UNSPECIFIED_SCENARIO: PromptContext(),
    COMPUTER_SCIENCE_SCENARIO: PromptContext(
        education_level=EducationLevel.UNDERGRADUATE,
        course_title="Data Structures",
        subject_area="Computer Science",
        material_kind=MaterialKind.LECTURE_NOTES,
    ),
}


def render(name: str, scenario: str) -> str:
    context = RENDER_SCENARIOS[scenario]
    return PromptLoader.render(
        name,
        {**context.as_variables(), **TEMPLATE_EXTRA_VARIABLES[name]},
        allow_deferred=True,
    )


def test_every_template_declares_a_render_fixture() -> None:
    templates = PromptLoader.load_all()

    assert set(TEMPLATE_EXTRA_VARIABLES) == set(templates), (
        "Every production template must register a rendering fixture in "
        "TEMPLATE_EXTRA_VARIABLES. Missing: "
        f"{sorted(set(templates) - set(TEMPLATE_EXTRA_VARIABLES))}. Unknown: "
        f"{sorted(set(TEMPLATE_EXTRA_VARIABLES) - set(templates))}"
    )

    for name, template in sorted(templates.items()):
        expected = set(template.required_variables) - SHARED_VARIABLES
        assert set(TEMPLATE_EXTRA_VARIABLES[name]) == expected, (
            f"The fixture for '{name}' must supply exactly its declared "
            f"non-shared required variables. Expected {sorted(expected)}, got "
            f"{sorted(TEMPLATE_EXTRA_VARIABLES[name])}"
        )
        assert SHARED_VARIABLES <= set(template.required_variables), (
            f"Template '{name}' must require every shared learner-context "
            "variable so its level and discipline come from context"
        )


@pytest.mark.parametrize("scenario", sorted(RENDER_SCENARIOS))
@pytest.mark.parametrize("name", ALL_TEMPLATE_NAMES)
def test_every_template_renders_for_every_representative_context(
    name: str, scenario: str
) -> None:
    context = RENDER_SCENARIOS[scenario]
    rendered = render(name, scenario)

    assert "{{" not in rendered, (name, scenario)
    assert "}}" not in rendered, (name, scenario)

    for value in (
        context.education_level.value,
        context.course_title,
        context.subject_area,
        context.material_kind.value,
    ):
        assert value in rendered, (
            f"'{name}' rendered under {scenario} did not carry {value!r} "
            "from the supplied learner context"
        )


@pytest.mark.parametrize("name", ALL_TEMPLATE_NAMES)
def test_unspecified_context_never_falls_back_to_a_level_or_discipline(
    name: str,
) -> None:
    rendered = render(name, UNSPECIFIED_SCENARIO)

    assert_no_level_or_discipline_fallback(
        rendered, f"'{name}' rendered with an unspecified context"
    )


@pytest.mark.parametrize("name", ALL_TEMPLATE_NAMES)
def test_a_supplied_computer_science_context_is_allowed(name: str) -> None:
    rendered = render(name, COMPUTER_SCIENCE_SCENARIO)

    assert "Computer Science" in rendered, (
        f"'{name}' dropped the supplied Computer Science subject area. "
        "Neutrality means discipline comes from context, not that Computer "
        "Science is forbidden."
    )
    assert "Data Structures" in rendered
    assert "{{" not in rendered


# ---------------------------------------------------------------------------
# Layer 3: the scanner must be proven to fire
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected_rule"),
    [
        ("You are a university tutor.", "level_persona"),
        (
            "You are an expert Computer Science teaching assistant.",
            "discipline_persona",
        ),
        ("Write a summary for university students.", "level_role"),
        ("Explain this for a college learner.", "level_for"),
        ("Produce questions for Computer Science students.", "discipline_role"),
        ("Write an answer for Computer Science.", "discipline_for"),
        ("Summarise the following lecture notes.", "material_lecture_notes"),
        ('PROMPT = "You are a university Computer Science tutor."', "level_persona"),
        (
            "You are a helpful guide writing for\nundergraduate students.",
            "level_role",
        ),
        ("YOU ARE A UNIVERSITY TUTOR.", "level_persona"),
    ],
)
def test_the_scanner_rejects_a_hardcoded_assumption(
    text: str, expected_rule: str
) -> None:
    violations = find_neutrality_violations(text)

    assert violations, f"The scanner failed to reject: {text!r}"
    assert expected_rule in {violation.rule.name for violation in violations}, (
        f"{text!r} was rejected, but not by the {expected_rule} rule: "
        f"{sorted({violation.rule.name for violation in violations})}"
    )


@pytest.mark.parametrize(
    "text",
    [
        "Education level: {{EDUCATION_LEVEL}}\nSubject area: {{SUBJECT_AREA}}",
        'UNDERGRADUATE = "undergraduate"',
        "Adapt depth and terminology to the education level above.",
        "The material is lecture notes, so it may be condensed and shorthand.",
        "Possible material kinds include slides, a textbook, and lecture notes.",
        "Never assume an academic level, subject, or material type.",
        "Subject area: Computer Science",
        "Course: Data Structures",
    ],
)
def test_the_scanner_accepts_context_driven_wording(text: str) -> None:
    assert not find_neutrality_violations(text), (
        f"The scanner wrongly rejected context-driven wording: {text!r}"
    )


def test_a_scanner_failure_names_the_surface_the_rule_and_the_guidance() -> None:
    with pytest.raises(AssertionError) as exc_info:
        assert_source_neutral(
            "line one\nYou are a university tutor.\n",
            "app/prompts/example.json (template)",
            with_lines=True,
        )

    message = str(exc_info.value)
    assert "app/prompts/example.json (template):2" in message
    assert "level_persona" in message
    assert "EDUCATION_LEVEL" in message


def test_a_directive_resolving_unspecified_to_a_level_would_be_rejected() -> None:
    smuggled = "Write for an undergraduate learner."

    assert LEVEL_VOCABULARY.search(smuggled), (
        "The unspecified-level assertion must reject a directive naming a tier"
    )
    assert find_neutrality_violations(smuggled)


def test_an_inline_prompt_in_a_production_module_is_rejected(tmp_path) -> None:
    module = tmp_path / "generation.py"
    module.write_text(
        "def build_prompt(material):\n"
        "    return (\n"
        '        "You are a university Computer Science tutor. "\n'
        '        "Summarise the material below."\n'
        "    )\n",
        encoding="utf-8",
    )

    violations = find_neutrality_violations(
        module.read_text(encoding="utf-8"), exempt=directive_exemptions()
    )

    assert violations, "An inline production prompt must not escape the scanner"
    assert violations[0].line == 3


def test_the_exemption_list_cannot_hide_a_hardcoded_assumption() -> None:
    assert find_neutrality_violations(
        "You are a university tutor.",
        exempt=["Write for an undergraduate learner."],
    )
