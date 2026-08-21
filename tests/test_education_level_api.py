from uuid import UUID

from backend.app.models import Course, UploadedDocument
from schemas.prompt_context import EducationLevel


def test_course_create_accepts_a_normalized_education_level(authz_api) -> None:
    response = authz_api.client.post(
        "/api/courses",
        headers=authz_api.authorization_a,
        json={
            "title": "AP Biology",
            "education_level": "high_school",
            "subject_area": "Biology",
        },
    )

    assert response.status_code in (200, 201), response.text
    data = response.json()["data"]
    assert data["education_level"] == "high_school"
    assert data["subject_area"] == "Biology"

    with authz_api.session_factory() as session:
        course = session.get(Course, data["id"])
        assert course.education_level == "high_school"
        assert course.subject_area == "Biology"


def test_course_create_without_a_level_is_unspecified_not_university(
    authz_api,
) -> None:
    response = authz_api.client.post(
        "/api/courses",
        headers=authz_api.authorization_a,
        json={"title": "Personal Study Workspace"},
    )

    assert response.status_code in (200, 201), response.text
    data = response.json()["data"]
    assert data["education_level"] == "unspecified"
    assert data["subject_area"] is None

    with authz_api.session_factory() as session:
        course = session.get(Course, data["id"])
        assert course.education_level == "unspecified"
        assert type(course.education_level) is str
        assert course.subject_area is None


def test_course_create_rejects_an_unnormalized_education_level(authz_api) -> None:
    response = authz_api.client.post(
        "/api/courses",
        headers=authz_api.authorization_a,
        json={"title": "Some Course", "education_level": "university"},
    )

    assert response.status_code == 422


def test_course_update_changes_the_education_level(authz_api) -> None:
    response = authz_api.client.put(
        f"/api/courses/{authz_api.a_course_id}",
        headers=authz_api.authorization_a,
        json={"education_level": "graduate", "subject_area": "Economics"},
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["education_level"] == "graduate"
    assert data["subject_area"] == "Economics"


def test_existing_course_reads_back_as_unspecified(authz_api) -> None:
    response = authz_api.client.get(
        f"/api/courses/{authz_api.a_course_id}",
        headers=authz_api.authorization_a,
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["education_level"] == "unspecified"


def test_user_education_level_endpoint_updates_the_profile(authz_api) -> None:
    response = authz_api.client.put(
        "/api/users/me/education-level",
        headers=authz_api.authorization_a,
        params={"education_level": "professional_other"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["education_level"] == "professional_other"

    follow_up = authz_api.client.get(
        "/api/users/me/credits", headers=authz_api.authorization_a
    )
    assert follow_up.status_code == 200


def test_user_education_level_endpoint_rejects_free_text(authz_api) -> None:
    response = authz_api.client.put(
        "/api/users/me/education-level",
        headers=authz_api.authorization_a,
        params={"education_level": "2nd year uni"},
    )

    assert response.status_code == 422


def test_upload_records_a_normalized_material_kind(authz_api) -> None:
    response = authz_api.client.post(
        f"/api/courses/{authz_api.a_course_id}/documents",
        headers=authz_api.authorization_a,
        files={"document": ("chapter.txt", b"Textbook chapter", "text/plain")},
        data={"material_kind": "textbook"},
    )

    assert response.status_code == 201, response.text
    document = response.json()["document"]
    assert document["material_kind"] == "textbook"

    with authz_api.session_factory() as session:
        persisted = session.get(UploadedDocument, UUID(document["id"]))
        assert persisted.material_kind == "textbook"


def test_upload_defaults_material_kind_to_unspecified(authz_api) -> None:
    response = authz_api.client.post(
        f"/api/courses/{authz_api.a_course_id}/documents",
        headers=authz_api.authorization_a,
        files={"document": ("plain.txt", b"Some other material", "text/plain")},
    )

    assert response.status_code == 201, response.text
    assert response.json()["document"]["material_kind"] == "unspecified"


def test_upload_rejects_mixed_as_a_document_material_kind(authz_api) -> None:
    response = authz_api.client.post(
        f"/api/courses/{authz_api.a_course_id}/documents",
        headers=authz_api.authorization_a,
        files={"document": ("mixed.txt", b"Mixed material", "text/plain")},
        data={"material_kind": "mixed"},
    )

    assert response.status_code == 422


def test_every_education_level_value_is_accepted(authz_api) -> None:
    for level in EducationLevel:
        response = authz_api.client.put(
            f"/api/courses/{authz_api.a_course_id}",
            headers=authz_api.authorization_a,
            json={"education_level": level.value},
        )
        assert response.status_code == 200, (level, response.text)
        assert response.json()["data"]["education_level"] == level.value
