"""Citations persist with their generated output and survive a reopen.

A reopen serves stored rows, so it must render the same sources without a
second provider call, and it must keep rendering them after the document they
name is gone: a citation is a true statement about what the generation read,
not a live link into the corpus.
"""

import pytest
from sqlalchemy import select

import routes.course_qa as course_qa_route
import routes.quiz as quiz_route
from backend.app.models import (
    Conversation,
    ConversationMessage,
    Course,
    DocumentChunk,
    Quiz,
    QuizQuestion,
    UploadedDocument,
    User,
)
from schemas.conversation import ConversationType
from services.conversation import parse_message_citations
from services.quiz import parse_citations
from services.text_generation import GenerationMetadata


def _seed_material(session, *, user, course, retrieval_env, file_name, text):
    document = UploadedDocument(
        original_file_name=file_name,
        file_type="txt",
        mime_type="text/plain",
        file_size=10,
        file_hash="d" * 64,
        uploader=user,
        course=course,
        storage_provider="local:test",
        storage_key="cited.txt",
        status="ready",
    )
    session.add(document)
    session.flush()
    chunk = DocumentChunk(
        document=document,
        course=course,
        chunk_index=0,
        page_number=12,
        end_page_number=12,
        text=text,
    )
    session.add(chunk)
    session.flush()
    retrieval_env.index(session, document, [chunk])
    session.commit()
    return document


@pytest.fixture
def cited_course(upload_api, retrieval_env):
    with upload_api.session_factory() as session:
        user = session.get(User, upload_api.user_id)
        course = session.get(Course, upload_api.course_id)
        document = _seed_material(
            session,
            user=user,
            course=course,
            retrieval_env=retrieval_env,
            file_name="lecture-04.pdf",
            text="Binary search halves the range each step.",
        )
        return {"course_id": course.id, "document_id": document.id}


def _install_citing_qa_provider(monkeypatch):
    class CitingProvider:
        def generate_text_with_metadata(self, prompt: str):
            return "Binary search halves the range. [S1]", GenerationMetadata(
                provider="ollama", model="llama3.1", latency_ms=10
            )

    monkeypatch.setattr(
        course_qa_route, "get_text_generation_provider", lambda **_: CitingProvider()
    )


def _ask(upload_api, course_id):
    response = upload_api.client.post(
        f"/api/courses/{course_id}/qa",
        json={"question": "How does binary search work?"},
        headers=upload_api.authorization,
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_reopening_a_thread_returns_each_assistant_message_citations(
    upload_api, cited_course, monkeypatch
) -> None:
    _install_citing_qa_provider(monkeypatch)
    generated = _ask(upload_api, cited_course["course_id"])

    def forbidden():
        raise AssertionError("reading a thread must never call a provider")

    monkeypatch.setattr(course_qa_route, "get_text_generation_provider", forbidden)

    response = upload_api.client.get(
        f"/api/courses/{cited_course['course_id']}"
        f"/conversations/{generated['conversation_id']}",
        headers=upload_api.authorization,
    )

    assert response.status_code == 200, response.text
    messages = response.json()["data"]["messages"]
    assistant = [message for message in messages if message["role"] == "assistant"]

    assert assistant[0]["citations"] == generated["citations"]
    assert assistant[0]["citations"][0]["document_label"] == "Lecture 4"


def test_a_user_message_carries_no_citations(
    upload_api, cited_course, monkeypatch
) -> None:
    _install_citing_qa_provider(monkeypatch)
    generated = _ask(upload_api, cited_course["course_id"])

    response = upload_api.client.get(
        f"/api/courses/{cited_course['course_id']}"
        f"/conversations/{generated['conversation_id']}",
        headers=upload_api.authorization,
    )

    messages = response.json()["data"]["messages"]
    user_messages = [message for message in messages if message["role"] == "user"]

    assert user_messages
    assert all(message["citations"] == [] for message in user_messages)


def test_a_citation_survives_deleting_the_document_it_names(
    upload_api, cited_course, monkeypatch
) -> None:
    _install_citing_qa_provider(monkeypatch)
    generated = _ask(upload_api, cited_course["course_id"])

    with upload_api.session_factory() as session:
        document = session.get(UploadedDocument, cited_course["document_id"])
        session.delete(document)
        session.commit()

    response = upload_api.client.get(
        f"/api/courses/{cited_course['course_id']}"
        f"/conversations/{generated['conversation_id']}",
        headers=upload_api.authorization,
    )

    assert response.status_code == 200, response.text
    messages = response.json()["data"]["messages"]
    assistant = [message for message in messages if message["role"] == "assistant"]

    assert assistant[0]["citations"][0]["document_label"] == "Lecture 4"
    assert assistant[0]["citations"][0]["page_start"] == 12


def test_a_malformed_stored_message_citation_reads_as_no_citations(
    upload_api, db_session
) -> None:
    conversation = Conversation(
        user_id=upload_api.user_id,
        course_id=upload_api.course_id,
        conversation_type=ConversationType.COURSE_QA.value,
    )
    db_session.add(conversation)
    db_session.flush()
    message = ConversationMessage(
        conversation=conversation,
        role="assistant",
        content="Broken",
        citations=["not-a-citation-document"],
    )
    db_session.add(message)
    db_session.flush()

    assert parse_message_citations(message) == []


def test_a_malformed_stored_question_citation_reads_as_no_citations(
    upload_api, db_session
) -> None:
    quiz = Quiz(course_id=upload_api.course_id, title="Broken")
    db_session.add(quiz)
    db_session.flush()
    row = QuizQuestion(
        quiz_id=quiz.id,
        question_index=0,
        question_type="true_false",
        question_text="Is it readable?",
        citations=[{"key": "S1"}],
    )
    db_session.add(row)
    db_session.flush()

    assert parse_citations(row) == []


def test_reopening_a_quiz_returns_its_citations_without_calling_a_provider(
    upload_api, cited_course, monkeypatch
) -> None:
    class CitingQuizProvider:
        def generate_json_with_metadata(self, prompt: str):
            return {
                "title": "Example Quiz",
                "questions": [
                    {
                        "question_number": 1,
                        "question_type": "multiple_choice",
                        "topic": "Search",
                        "question": "What does binary search do?",
                        "difficulty": "medium",
                        "options": ["Halves", "Doubles", "Sorts", "Hashes"],
                        "correct_option_index": 0,
                        "explanation": "The material says so.",
                        "citations": ["S1", "S99"],
                    }
                ],
            }, GenerationMetadata(provider="ollama", model="llama3.1", latency_ms=10)

    monkeypatch.setattr(
        quiz_route, "get_text_generation_provider", lambda **_: CitingQuizProvider()
    )

    response = upload_api.client.post(
        f"/api/courses/{cited_course['course_id']}/quiz",
        json={
            "question_count": 1,
            "question_types": ["multiple_choice"],
            "difficulty": "medium",
            "topic_focus": "All Topics",
        },
        headers=upload_api.authorization,
    )
    assert response.status_code == 200, response.text
    quiz_id = response.json()["data"]["quiz"]["quiz_id"]

    def forbidden(**_):
        raise AssertionError("reading a quiz must never call a provider")

    monkeypatch.setattr(quiz_route, "get_text_generation_provider", forbidden)

    reopened = upload_api.client.get(
        f"/api/courses/{cited_course['course_id']}/quizzes/{quiz_id}",
        headers=upload_api.authorization,
    )

    assert reopened.status_code == 200, reopened.text
    citations = reopened.json()["data"]["questions"][0]["citations"]

    assert [citation["key"] for citation in citations] == ["S1"]
    assert citations[0]["document_label"] == "Lecture 4"
    assert citations[0]["page_start"] == 12


def test_a_persisted_quiz_question_stores_its_citations(
    upload_api, cited_course, monkeypatch
) -> None:
    test_reopening_a_quiz_returns_its_citations_without_calling_a_provider(
        upload_api, cited_course, monkeypatch
    )

    with upload_api.session_factory() as session:
        stored = session.scalars(select(QuizQuestion)).all()

    assert stored
    assert stored[0].citations[0]["key"] == "S1"
