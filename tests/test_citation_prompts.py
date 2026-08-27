import pytest

from services.prompt_loader import PromptLoader

CITING_TEMPLATES = (
    "study_guide",
    "quiz",
    "course_qa",
    "ai_tutor",
    "exam_topic_analysis",
    "past_exam_question_extraction",
    "exam_topic_guide",
    "exam_topic_summary",
)


@pytest.mark.parametrize("name", CITING_TEMPLATES)
def test_every_citing_template_explains_the_citation_keys(name: str) -> None:
    template = PromptLoader.load_template(name, reload=True)

    assert "SOURCE CITATIONS" in template.template
    assert "[S1]" in template.template
    assert "Never invent a key" in template.template


@pytest.mark.parametrize("name", CITING_TEMPLATES)
def test_every_citing_template_forbids_an_unsupplied_key(name: str) -> None:
    template = PromptLoader.load_template(name, reload=True)

    assert any(
        "citation key that does not appear in the supplied material" in constraint
        for constraint in template.safety_constraints
    )


def test_the_flashcard_template_asks_for_no_citations() -> None:
    template = PromptLoader.load_template("flashcard", reload=True)

    assert "SOURCE CITATIONS" not in template.template
    assert "[S1]" not in template.template


def test_the_study_guide_schema_puts_citations_on_every_citable_field() -> None:
    template = PromptLoader.load_template("study_guide", reload=True).template

    assert '"summary": {\n    "text": "",\n    "citations": []\n  }' in template
    assert template.count('"citations": []') == 7
    assert '"ai_suggestions": []' in template


def test_the_quiz_schema_puts_citations_on_every_question_type() -> None:
    from services.quiz import QUESTION_TYPE_SCHEMAS

    assert len(QUESTION_TYPE_SCHEMAS) == 4
    for schema in QUESTION_TYPE_SCHEMAS.values():
        assert '"citations": ["S1"]' in schema
