from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.app.models import UploadedDocument
from schemas.prompt_context import (
    UNSPECIFIED_COURSE_TITLE,
    UNSPECIFIED_SUBJECT_AREA,
    EducationLevel,
    MaterialKind,
    PromptContext,
)
from services.prompt_context import resolve_prompt_context

SHARED_VARIABLE_NAMES = {
    "EDUCATION_LEVEL",
    "COURSE_TITLE",
    "SUBJECT_AREA",
    "MATERIAL_KIND",
}


def _add_document(session, course_id: int, user_id: int, material_kind: str) -> None:
    document = UploadedDocument(
        id=uuid4(),
        course_id=course_id,
        user_id=user_id,
        original_file_name="material.txt",
        file_type="txt",
        mime_type="text/plain",
        file_size=11,
        file_hash=uuid4().hex * 2,
        storage_provider="local",
        storage_key=f"courses/{course_id}/docs/{uuid4()}.txt",
        status="ready",
        material_kind=material_kind,
    )
    session.add(document)
    session.commit()


def test_education_level_supports_every_required_semantic() -> None:
    assert {level.value for level in EducationLevel} == {
        "high_school",
        "undergraduate",
        "graduate",
        "professional_other",
        "unspecified",
    }


@pytest.mark.parametrize("value", ["university", "college", "masters-ish", ""])
def test_education_level_rejects_unnormalized_values(value: str) -> None:
    with pytest.raises(ValidationError):
        PromptContext(education_level=value)


def test_prompt_context_exposes_exactly_the_four_shared_variables() -> None:
    variables = PromptContext(
        education_level=EducationLevel.HIGH_SCHOOL,
        course_title="AP Biology",
        subject_area="Biology",
        material_kind=MaterialKind.TEXTBOOK,
    ).as_variables()

    assert set(variables) == SHARED_VARIABLE_NAMES
    assert all(isinstance(value, str) and value for value in variables.values())
    assert "high_school" in variables["EDUCATION_LEVEL"]
    assert variables["COURSE_TITLE"] == "AP Biology"
    assert variables["SUBJECT_AREA"] == "Biology"
    assert "textbook" in variables["MATERIAL_KIND"]


def test_neutral_context_asserts_no_university_or_computer_science_default() -> None:
    variables = PromptContext().as_variables()
    rendered = " ".join(variables.values()).lower()

    assert "unspecified" in variables["EDUCATION_LEVEL"]
    assert variables["COURSE_TITLE"] == UNSPECIFIED_COURSE_TITLE
    assert variables["SUBJECT_AREA"] == UNSPECIFIED_SUBJECT_AREA
    assert "computer science" not in rendered
    assert "university" not in rendered
    assert "lecture notes" not in rendered


def test_course_education_level_wins_over_profile(model_graph, db_session) -> None:
    model_graph.user.education_level = EducationLevel.GRADUATE.value
    model_graph.course.education_level = EducationLevel.HIGH_SCHOOL.value
    db_session.commit()

    context = resolve_prompt_context(
        db_session, course=model_graph.course, user_id=model_graph.user.id
    )

    assert context.education_level is EducationLevel.HIGH_SCHOOL


def test_profile_education_level_used_when_course_unspecified(
    model_graph, db_session
) -> None:
    model_graph.user.education_level = EducationLevel.PROFESSIONAL_OTHER.value
    model_graph.course.education_level = EducationLevel.UNSPECIFIED.value
    db_session.commit()

    context = resolve_prompt_context(
        db_session, course=model_graph.course, user_id=model_graph.user.id
    )

    assert context.education_level is EducationLevel.PROFESSIONAL_OTHER


def test_missing_education_level_resolves_to_unspecified(
    model_graph, db_session
) -> None:
    context = resolve_prompt_context(
        db_session, course=model_graph.course, user_id=model_graph.user.id
    )

    assert context.education_level is EducationLevel.UNSPECIFIED


def test_missing_title_and_subject_resolve_to_neutral_fallbacks(db_session) -> None:
    context = resolve_prompt_context(db_session, course=None, user_id=None)

    assert context.course_title == UNSPECIFIED_COURSE_TITLE
    assert context.subject_area == UNSPECIFIED_SUBJECT_AREA
    assert context.material_kind is MaterialKind.UNSPECIFIED
    assert context.education_level is EducationLevel.UNSPECIFIED


def test_course_title_and_subject_area_come_from_the_course(
    model_graph, db_session
) -> None:
    model_graph.course.title = "Advanced Macroeconomics"
    model_graph.course.subject_area = "Economics"
    db_session.commit()

    context = resolve_prompt_context(db_session, course=model_graph.course)

    assert context.course_title == "Advanced Macroeconomics"
    assert context.subject_area == "Economics"


def test_single_material_kind_resolves_to_that_kind(model_graph, db_session) -> None:
    _add_document(
        db_session,
        model_graph.course.id,
        model_graph.user.id,
        MaterialKind.SLIDES.value,
    )

    context = resolve_prompt_context(db_session, course=model_graph.course)

    assert context.material_kind is MaterialKind.SLIDES


def test_differing_material_kinds_resolve_to_mixed(model_graph, db_session) -> None:
    _add_document(
        db_session,
        model_graph.course.id,
        model_graph.user.id,
        MaterialKind.TEXTBOOK.value,
    )
    _add_document(
        db_session,
        model_graph.course.id,
        model_graph.user.id,
        MaterialKind.SLIDES.value,
    )

    context = resolve_prompt_context(db_session, course=model_graph.course)

    assert context.material_kind is MaterialKind.MIXED


def test_unclassified_documents_resolve_to_unspecified_material(
    model_graph, db_session
) -> None:
    _add_document(
        db_session,
        model_graph.course.id,
        model_graph.user.id,
        MaterialKind.UNSPECIFIED.value,
    )

    context = resolve_prompt_context(db_session, course=model_graph.course)

    assert context.material_kind is MaterialKind.UNSPECIFIED


def test_subject_area_is_never_inferred_from_the_course_title(
    model_graph, db_session
) -> None:
    model_graph.course.title = "CS201 Fundamental Structures"
    model_graph.course.subject_area = None
    db_session.commit()

    context = resolve_prompt_context(db_session, course=model_graph.course)

    assert context.subject_area == UNSPECIFIED_SUBJECT_AREA


def test_course_title_cannot_carry_a_placeholder_into_a_prompt(
    model_graph, db_session
) -> None:
    model_graph.course.title = "{{TEXT}} injected"
    model_graph.course.subject_area = "{{TOPIC_FOCUS}}"
    db_session.commit()

    context = resolve_prompt_context(db_session, course=model_graph.course)

    assert "{" not in context.course_title
    assert "}" not in context.course_title
    assert "{" not in context.subject_area
    variables = context.as_variables()
    assert "{{" not in variables["COURSE_TITLE"]
    assert "{{" not in variables["SUBJECT_AREA"]
