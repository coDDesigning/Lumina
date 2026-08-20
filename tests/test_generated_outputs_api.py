"""Reading the AI artifacts a course has already generated.

These endpoints are reads, so the administrator override applies: an admin may
read another owner's history but still cannot generate into it. A stored output
is always scoped to its parent course, so an identifier from another course is
indistinguishable from one that does not exist.
"""

import json

import pytest
from sqlalchemy import select

from backend.app.models import GeneratedOutput

STUDY_GUIDE_CONTENT = {
    "title": "Stored Guide",
    "summary": "Stored summary",
    "key_points": ["one"],
    "important_terms": [],
    "common_mistakes": [],
    "exam_tips": {"lecture_based": [], "ai_suggestions": []},
    "difficulty": {"level": "Easy", "reason": "Introductory material"},
    "estimated_study_time": "20 minutes",
    "prerequisites": [],
    "learning_objectives": [],
    "coverage": {"status": "Complete", "estimated_completeness": 100},
    "confidence_notes": "",
}

GENERATION_SETTINGS = {
    "version": 1,
    "output_type": "study_guide",
    "summary_format": "exam_tips",
    "topic_focus": "Graphs",
    "summary_length": "long",
    "detail_level": "detailed",
    "summary_mode": "exam_focused",
    "retrieval_limit": 24,
    "retrieval_min_similarity": 0.25,
}

GENERATION_CONTEXT = {
    "version": 1,
    "chunks_ranked": 24,
    "chunks_retrieved": 18,
    "chunks_used": 18,
    "chunks_available": 200,
    "lowest_similarity": 0.41,
    "highest_similarity": 0.88,
    "truncated": False,
}


def _store_output(
    session_factory,
    course_id: int,
    *,
    user_id: int | None = None,
    output_type: str = "study_guide",
    content: str | None = None,
    settings: str | None = None,
    context: str | None = None,
    model_used: str | None = "ollama:qwen3:8b",
) -> int:
    with session_factory() as session:
        output = GeneratedOutput(
            course_id=course_id,
            user_id=user_id,
            model_used=model_used,
            output_type=output_type,
            content=content if content is not None else json.dumps(STUDY_GUIDE_CONTENT),
            generation_settings=settings,
            generation_context=context,
        )
        session.add(output)
        session.commit()
        return output.id


def _list_url(course_id: int) -> str:
    return f"/api/courses/{course_id}/generated-outputs"


def _detail_url(course_id: int, output_id: int) -> str:
    return f"/api/courses/{course_id}/generated-outputs/{output_id}"


def test_owner_lists_generated_outputs_newest_first(upload_api) -> None:
    first = _store_output(upload_api.session_factory, upload_api.course_id)
    second = _store_output(upload_api.session_factory, upload_api.course_id)

    response = upload_api.client.get(
        _list_url(upload_api.course_id), headers=upload_api.authorization
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["success"] is True
    assert [entry["id"] for entry in payload["data"]] == [second, first]


def test_generated_output_list_omits_content(upload_api) -> None:
    """A history listing must stay small; the content belongs to the detail read."""
    _store_output(upload_api.session_factory, upload_api.course_id)

    response = upload_api.client.get(
        _list_url(upload_api.course_id), headers=upload_api.authorization
    )

    assert response.status_code == 200, response.text
    assert "content" not in response.json()["data"][0]


def test_generated_output_list_is_empty_for_a_course_without_outputs(
    upload_api,
) -> None:
    response = upload_api.client.get(
        _list_url(upload_api.course_id), headers=upload_api.authorization
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"] == []


def test_generated_output_list_is_scoped_to_its_course(upload_api) -> None:
    mine = _store_output(upload_api.session_factory, upload_api.course_id)
    _store_output(upload_api.session_factory, upload_api.other_course_id)

    response = upload_api.client.get(
        _list_url(upload_api.course_id), headers=upload_api.authorization
    )

    assert [entry["id"] for entry in response.json()["data"]] == [mine]


def test_owner_reads_a_generated_output_detail(upload_api) -> None:
    output_id = _store_output(
        upload_api.session_factory,
        upload_api.course_id,
        user_id=upload_api.user_id,
        settings=json.dumps(GENERATION_SETTINGS),
        context=json.dumps(GENERATION_CONTEXT),
    )

    response = upload_api.client.get(
        _detail_url(upload_api.course_id, output_id), headers=upload_api.authorization
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert data["id"] == output_id
    assert data["course_id"] == upload_api.course_id
    assert data["output_type"] == "study_guide"
    assert data["user_id"] == upload_api.user_id
    assert data["model_used"] == "ollama:qwen3:8b"
    assert data["created_at"]
    assert data["content"] == STUDY_GUIDE_CONTENT
    assert data["generation_settings"] == GENERATION_SETTINGS
    assert data["generation_context"] == GENERATION_CONTEXT


def test_generated_output_detail_reports_null_settings_for_a_legacy_row(
    upload_api,
) -> None:
    """Rows written before the settings columns existed are never backfilled."""
    output_id = _store_output(
        upload_api.session_factory, upload_api.course_id, model_used=None
    )

    response = upload_api.client.get(
        _detail_url(upload_api.course_id, output_id), headers=upload_api.authorization
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["generation_settings"] is None
    assert data["generation_context"] is None
    assert data["model_used"] is None
    assert data["user_id"] is None


@pytest.mark.parametrize(
    "stored", ["not json at all", '{"unterminated": ', '["a", "list"]', '"a string"']
)
def test_generated_output_detail_survives_unparseable_stored_settings(
    upload_api, stored: str
) -> None:
    output_id = _store_output(
        upload_api.session_factory,
        upload_api.course_id,
        settings=stored,
        context=stored,
    )

    response = upload_api.client.get(
        _detail_url(upload_api.course_id, output_id), headers=upload_api.authorization
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["generation_settings"] is None
    assert data["generation_context"] is None


def test_generated_output_detail_survives_unparseable_stored_content(
    upload_api,
) -> None:
    """One unreadable row must not break the whole history view."""
    output_id = _store_output(
        upload_api.session_factory, upload_api.course_id, content="not json at all"
    )

    response = upload_api.client.get(
        _detail_url(upload_api.course_id, output_id), headers=upload_api.authorization
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["content"] == "not json at all"


def test_generated_output_detail_returns_other_output_types_unchanged(
    upload_api,
) -> None:
    output_id = _store_output(
        upload_api.session_factory,
        upload_api.course_id,
        output_type="flashcards",
        content=json.dumps({"cards": []}),
    )

    response = upload_api.client.get(
        _detail_url(upload_api.course_id, output_id), headers=upload_api.authorization
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["output_type"] == "flashcards"
    assert data["content"] == {"cards": []}


def test_missing_generated_output_is_not_found(upload_api) -> None:
    response = upload_api.client.get(
        _detail_url(upload_api.course_id, 999999), headers=upload_api.authorization
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Generated output not found"}


def test_a_generated_output_from_another_course_is_not_found(upload_api) -> None:
    """The caller owns both courses, but the URL scopes the output to one of them."""
    output_id = _store_output(upload_api.session_factory, upload_api.other_course_id)

    response = upload_api.client.get(
        _detail_url(upload_api.course_id, output_id), headers=upload_api.authorization
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Generated output not found"}


def test_generated_outputs_of_a_tombstoned_course_are_not_found(upload_api) -> None:
    output_id = _store_output(upload_api.session_factory, upload_api.deleted_course_id)

    listing = upload_api.client.get(
        _list_url(upload_api.deleted_course_id), headers=upload_api.authorization
    )
    detail = upload_api.client.get(
        _detail_url(upload_api.deleted_course_id, output_id),
        headers=upload_api.authorization,
    )

    assert listing.status_code == 404
    assert listing.json() == {"detail": "Course not found"}
    assert detail.status_code == 404
    assert detail.json() == {"detail": "Course not found"}


def test_generated_outputs_of_a_missing_course_are_not_found(upload_api) -> None:
    listing = upload_api.client.get(_list_url(999999), headers=upload_api.authorization)
    detail = upload_api.client.get(
        _detail_url(999999, 1), headers=upload_api.authorization
    )

    assert listing.status_code == 404
    assert listing.json() == {"detail": "Course not found"}
    assert detail.status_code == 404
    assert detail.json() == {"detail": "Course not found"}


def test_generated_output_routes_require_authentication(api_context) -> None:
    listing = api_context.client.get(_list_url(1))
    detail = api_context.client.get(_detail_url(1, 1))

    assert listing.status_code == 401
    assert detail.status_code == 401


def test_another_owner_cannot_reach_generated_outputs(authz_api) -> None:
    output_id = _store_output(authz_api.session_factory, authz_api.a_course_id)

    listing = authz_api.client.get(
        _list_url(authz_api.a_course_id), headers=authz_api.authorization_b
    )
    detail = authz_api.client.get(
        _detail_url(authz_api.a_course_id, output_id),
        headers=authz_api.authorization_b,
    )

    assert listing.status_code == 404
    assert listing.json() == {"detail": "Course not found"}
    assert detail.status_code == 404
    assert detail.json() == {"detail": "Course not found"}


def test_administrator_may_read_another_owners_generated_outputs(authz_api) -> None:
    output_id = _store_output(
        authz_api.session_factory,
        authz_api.a_course_id,
        user_id=authz_api.user_a_id,
        settings=json.dumps(GENERATION_SETTINGS),
    )

    listing = authz_api.client.get(
        _list_url(authz_api.a_course_id), headers=authz_api.authorization_admin
    )
    detail = authz_api.client.get(
        _detail_url(authz_api.a_course_id, output_id),
        headers=authz_api.authorization_admin,
    )

    assert listing.status_code == 200, listing.text
    assert [entry["id"] for entry in listing.json()["data"]] == [output_id]
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["generation_settings"] == GENERATION_SETTINGS


def test_reading_a_generated_output_never_regenerates_it(
    upload_api, monkeypatch
) -> None:
    """History is served from the database; no AI provider is involved."""
    import routes.study_guide as study_guide_route

    def forbidden():
        raise AssertionError("reading history must never call a provider")

    monkeypatch.setattr(study_guide_route, "get_text_generation_provider", forbidden)
    output_id = _store_output(upload_api.session_factory, upload_api.course_id)

    response = upload_api.client.get(
        _detail_url(upload_api.course_id, output_id), headers=upload_api.authorization
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["content"] == STUDY_GUIDE_CONTENT


def test_reading_a_generated_output_does_not_change_stored_rows(upload_api) -> None:
    output_id = _store_output(
        upload_api.session_factory,
        upload_api.course_id,
        settings=json.dumps(GENERATION_SETTINGS),
    )

    upload_api.client.get(
        _detail_url(upload_api.course_id, output_id), headers=upload_api.authorization
    )

    with upload_api.session_factory() as session:
        rows = session.scalars(
            select(GeneratedOutput).where(
                GeneratedOutput.course_id == upload_api.course_id
            )
        ).all()

    assert len(rows) == 1
    assert rows[0].generation_settings == json.dumps(GENERATION_SETTINGS)
