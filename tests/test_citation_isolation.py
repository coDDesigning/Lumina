"""A citation may only ever name a document of the course that was asked.

Course isolation is already enforced at three layers, but citations are the
first feature that renders a document's identity back to the student, so a
leak here would be visible rather than merely present in a prompt.
"""

import pytest
from sqlalchemy import select

import routes.course_qa as course_qa_route
from backend.app.models import Course, DocumentChunk, UploadedDocument, User
from services.text_generation import GenerationMetadata


def _add_ready_document(
    session,
    *,
    user: User,
    course: Course,
    file_hash: str,
    text: str,
    file_name: str,
    retrieval_env,
    pages: tuple[int, int] | None = None,
) -> UploadedDocument:
    document = UploadedDocument(
        original_file_name=file_name,
        file_type="txt",
        mime_type="text/plain",
        file_size=10,
        file_hash=file_hash,
        uploader=user,
        course=course,
        storage_provider="local:test",
        storage_key=f"{file_hash}.txt",
        status="ready",
    )
    session.add(document)
    session.flush()
    chunk = DocumentChunk(
        document=document,
        course=course,
        chunk_index=0,
        page_number=pages[0] if pages else None,
        end_page_number=pages[1] if pages else None,
        text=text,
    )
    session.add(chunk)
    session.flush()
    retrieval_env.index(session, document, [chunk])
    session.commit()
    return document


@pytest.fixture
def two_courses(upload_api, retrieval_env):
    with upload_api.session_factory() as session:
        user = session.get(User, upload_api.user_id)
        first = session.get(Course, upload_api.course_id)
        assert user is not None and first is not None

        first_document = _add_ready_document(
            session,
            user=user,
            course=first,
            file_hash="a" * 64,
            text="Course one holds the physics formula.",
            file_name="physics-01.pdf",
            retrieval_env=retrieval_env,
            pages=(3, 3),
        )

        second = Course(owner=user, title="Course two chemistry")
        session.add(second)
        session.flush()
        second_document = _add_ready_document(
            session,
            user=user,
            course=second,
            file_hash="b" * 64,
            text="Course two holds the chemistry formula.",
            file_name="chemistry-07.pdf",
            retrieval_env=retrieval_env,
            pages=(9, 9),
        )
        return {
            "first_course_id": first.id,
            "second_course_id": second.id,
            "first_document_id": str(first_document.id),
            "second_document_id": str(second_document.id),
        }


def _install_citing_provider(monkeypatch, answer: str):
    class CitingProvider:
        def generate_text_with_metadata(self, prompt: str):
            return answer, GenerationMetadata(
                provider="ollama", model="llama3.1", latency_ms=10
            )

    monkeypatch.setattr(
        course_qa_route, "get_text_generation_provider", lambda **_: CitingProvider()
    )


def test_a_citation_never_names_a_document_from_another_course(
    upload_api, two_courses, monkeypatch
) -> None:
    _install_citing_provider(monkeypatch, "The formula is well known. [S1]")

    response = upload_api.client.post(
        f"/api/courses/{two_courses['second_course_id']}/qa",
        json={"question": "What is the formula?"},
        headers=upload_api.authorization,
    )

    assert response.status_code == 200, response.text
    citations = response.json()["data"]["citations"]

    assert citations
    for citation in citations:
        assert citation["document_id"] == two_courses["second_document_id"]
        assert citation["document_id"] != two_courses["first_document_id"]
        assert citation["document_label"] == "Chemistry 7"
        assert citation["page_start"] == 9


def test_the_same_key_resolves_to_a_different_document_in_each_course(
    upload_api, two_courses, monkeypatch
) -> None:
    """S1 is positional, so it names whichever course supplied it."""
    _install_citing_provider(monkeypatch, "The formula is well known. [S1]")

    labels = {}
    for name in ("first_course_id", "second_course_id"):
        response = upload_api.client.post(
            f"/api/courses/{two_courses[name]}/qa",
            json={"question": "What is the formula?"},
            headers=upload_api.authorization,
        )
        assert response.status_code == 200, response.text
        citations = response.json()["data"]["citations"]
        assert [citation["key"] for citation in citations] == ["S1"]
        labels[name] = citations[0]["document_label"]

    assert labels["first_course_id"] == "Physics 1"
    assert labels["second_course_id"] == "Chemistry 7"
    assert labels["first_course_id"] != labels["second_course_id"]


def test_a_citation_names_only_documents_the_course_actually_holds(
    upload_api, two_courses, monkeypatch
) -> None:
    _install_citing_provider(monkeypatch, "The formula is well known. [S1]")

    response = upload_api.client.post(
        f"/api/courses/{two_courses['second_course_id']}/qa",
        json={"question": "What is the formula?"},
        headers=upload_api.authorization,
    )
    assert response.status_code == 200, response.text

    cited = {
        citation["document_id"] for citation in response.json()["data"]["citations"]
    }
    with upload_api.session_factory() as session:
        owned = {
            str(document_id)
            for document_id in session.scalars(
                select(UploadedDocument.id).where(
                    UploadedDocument.course_id == two_courses["second_course_id"]
                )
            )
        }

    assert cited
    assert cited <= owned
