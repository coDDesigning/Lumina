"""Deleting a course erases it, and a failed erasure stays safe to retry.

``tests/test_database_contract.py`` proves the relational cascades against raw
SQL. This module proves the promise a student is actually given: one owner-
initiated request removes every artifact derived from the course, across storage
and the vector store as well as the database, and leaves that student's account
and profile knowledge untouched.
"""

import pytest
from sqlalchemy import func, select

from backend.app.models import (
    EMBEDDING_DIMENSIONS,
    AiUsageLog,
    ChunkEmbedding,
    Course,
    CreditTransaction,
    DocumentChunk,
    DocumentPage,
    DocumentVisual,
    GeneratedOutput,
    GenerationJob,
    ProcessingJob,
    ProfileKnowledge,
    Progress,
    Quiz,
    QuizAttempt,
    QuizAttemptAnswer,
    QuizQuestion,
    UploadedDocument,
    User,
)
from schemas.credits import CreditReason
from services.course import CourseService
from services.credits import CreditService
from services.generation_jobs import enqueue_generation_job
from services.vector_store import PgVectorStore, VectorRecord, VectorStoreError


def _stored_files(root) -> list[str]:
    return sorted(
        str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()
    )


def _count(session, model) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def _fill_course_graph(authz_api, store) -> dict[str, int]:
    """Give the owner's course one row in every course-scoped table."""
    with authz_api.session_factory() as session:
        course = session.get(Course, authz_api.a_course_id)
        user = session.get(User, authz_api.user_a_id)
        document = session.get(UploadedDocument, authz_api.a_document_id)
        assert course is not None and user is not None and document is not None

        page = DocumentPage(
            document_id=document.id,
            course_id=course.id,
            content_index=0,
            page_number=1,
            raw_text="Raw page text",
            text="Cleaned page text",
            has_images=True,
        )
        session.add(page)
        session.flush()
        visual = DocumentVisual(
            page_id=page.id,
            visual_index=0,
            visual_type="diagram",
            source="image",
            bbox_x0=0.0,
            bbox_y0=0.0,
            bbox_x1=10.0,
            bbox_y1=10.0,
        )
        chunk = DocumentChunk(
            document_id=document.id,
            course_id=course.id,
            chunk_index=0,
            text="Chunk of the owner's material",
        )
        generated_output = GeneratedOutput(
            course=course,
            user_id=user.id,
            output_type="summary",
            content="Owner A study guide",
            model_used="gemini-2.5-flash",
        )
        quiz = Quiz(course=course, title="Owner A quiz")
        question = QuizQuestion(
            quiz=quiz,
            question_index=0,
            question_text="Is this course deletable?",
            options=["Yes", "No"],
            correct_option_index=0,
        )
        attempt = QuizAttempt(user=user, quiz=quiz, score=1.0)
        answer = QuizAttemptAnswer(
            attempt=attempt,
            question=question,
            selected_option_index=0,
            is_correct=True,
        )
        progress = Progress(user=user, course=course, completion=0.75)
        usage_log = AiUsageLog(
            user=user,
            course=course,
            generation_type="quiz",
            provider="gemini",
            model="gemini-2.5-flash",
            success=True,
        )
        knowledge = ProfileKnowledge(
            user=user,
            topic="Recursion",
            detail="Understands base cases well.",
        )
        session.add_all(
            (
                visual,
                chunk,
                generated_output,
                question,
                attempt,
                answer,
                progress,
                usage_log,
                knowledge,
            )
        )
        session.flush()

        store.replace_document_vectors(
            session,
            document_id=document.id,
            course_id=course.id,
            records=[
                VectorRecord(
                    chunk_id=chunk.id,
                    document_id=document.id,
                    course_id=course.id,
                    chunk_index=chunk.chunk_index,
                    embedding=[0.5] * EMBEDDING_DIMENSIONS,
                )
            ],
            embedding_provider="ollama",
            embedding_model="nomic-embed-text",
        )
        session.commit()
        return {"knowledge_id": knowledge.id, "visual_id": visual.id}


@pytest.fixture
def graph(authz_api, monkeypatch):
    store = PgVectorStore()
    monkeypatch.setattr("services.course.get_vector_store", lambda: store)
    identifiers = _fill_course_graph(authz_api, store)
    return {"store": store, **identifiers}


def test_owner_delete_erases_every_course_artifact(authz_api, graph) -> None:
    store = graph["store"]
    with authz_api.session_factory() as session:
        assert store.count_course_vectors(session, authz_api.a_course_id) == 1
    assert _stored_files(authz_api.storage_root) != []

    deleted = authz_api.client.delete(
        f"/api/courses/{authz_api.a_course_id}", headers=authz_api.authorization_a
    )

    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["message"] == "Course permanently deleted"
    assert _stored_files(authz_api.storage_root) == []
    with authz_api.session_factory() as session:
        assert session.get(Course, authz_api.a_course_id) is None
        for model in (
            UploadedDocument,
            DocumentPage,
            DocumentVisual,
            DocumentChunk,
            ChunkEmbedding,
            ProcessingJob,
            GeneratedOutput,
            Quiz,
            QuizQuestion,
            QuizAttempt,
            QuizAttemptAnswer,
            Progress,
            AiUsageLog,
        ):
            assert _count(session, model) == 0, model.__name__
        assert store.count_course_vectors(session, authz_api.a_course_id) == 0


def test_finalize_hard_delete_rejects_an_invalid_operation_timeout(
    authz_api, graph
) -> None:
    with authz_api.session_factory() as session:
        with pytest.raises(ValueError):
            CourseService.finalize_hard_delete(
                session,
                authz_api.a_course_id,
                graph["store"],
                operation_timeout_seconds=-1,
            )


def test_finalize_hard_delete_accepts_a_widened_operation_timeout_on_sqlite(
    authz_api, graph
) -> None:
    """SQLite has no statement_timeout GUC, so the widened budget must be a no-op.

    This is the course-purge counterpart to the timeout override that already
    exists for ``services.processing_jobs.replace_document_pages`` - the same
    ``COURSE_PURGE_OPERATION_TIMEOUT_SECONDS`` value the worker passes must not
    break a course delete on the self-hosted default.
    """
    with authz_api.session_factory() as session:
        course = session.get(Course, authz_api.a_course_id)
        assert course is not None
        course.is_deleted = True
        session.commit()

    with authz_api.session_factory() as session:
        CourseService.finalize_hard_delete(
            session,
            authz_api.a_course_id,
            graph["store"],
            operation_timeout_seconds=300,
        )

    with authz_api.session_factory() as session:
        assert session.get(Course, authz_api.a_course_id) is None


def test_owner_delete_leaves_the_account_and_its_profile_knowledge(
    authz_api, graph
) -> None:
    """The privacy boundary runs around the course, not around the student."""
    deleted = authz_api.client.delete(
        f"/api/courses/{authz_api.a_course_id}", headers=authz_api.authorization_a
    )
    assert deleted.status_code == 200, deleted.text

    with authz_api.session_factory() as session:
        assert session.get(User, authz_api.user_a_id) is not None
        knowledge = session.get(ProfileKnowledge, graph["knowledge_id"])
        assert knowledge is not None
        assert knowledge.topic == "Recursion"
        assert knowledge.detail == "Understands base cases well."
        assert session.get(Course, authz_api.b_course_id) is not None

    listed = authz_api.client.get(
        "/api/profile-knowledge/", headers=authz_api.authorization_a
    )
    assert listed.status_code == 200
    assert any(item["topic"] == "Recursion" for item in listed.json()["data"])


def test_a_non_owner_delete_changes_nothing(authz_api, graph) -> None:
    denied = authz_api.client.delete(
        f"/api/courses/{authz_api.a_course_id}", headers=authz_api.authorization_b
    )

    assert denied.status_code == 404
    assert denied.json() == {"detail": "Course not found"}
    assert _stored_files(authz_api.storage_root) != []
    with authz_api.session_factory() as session:
        course = session.get(Course, authz_api.a_course_id)
        assert course is not None
        assert course.is_deleted is False
        assert graph["store"].count_course_vectors(session, authz_api.a_course_id) == 1


def test_an_administrator_cannot_delete_another_owners_course(authz_api, graph) -> None:
    """Reading any course is an administrator power. Deleting one is not."""
    readable = authz_api.client.get(
        f"/api/courses/{authz_api.a_course_id}", headers=authz_api.authorization_admin
    )
    assert readable.status_code == 200

    denied = authz_api.client.delete(
        f"/api/courses/{authz_api.a_course_id}", headers=authz_api.authorization_admin
    )

    assert denied.status_code == 404
    assert denied.json() == {"detail": "Course not found"}
    assert _stored_files(authz_api.storage_root) != []
    with authz_api.session_factory() as session:
        course = session.get(Course, authz_api.a_course_id)
        assert course is not None
        assert course.is_deleted is False


def test_a_vector_failure_retains_the_tombstone_and_the_retry_erases(
    authz_api, graph, monkeypatch
) -> None:
    """Vectors would outlive their rows if we reported success here, so we do not.

    Storage cleanup runs before the vector store, so the retry has to tolerate
    files that are already gone. Pinning the file count after the failure is what
    makes that resumption contract explicit.
    """
    store = graph["store"]

    class FailingStore:
        def delete_course_vectors(self, session, course_id):
            raise VectorStoreError("the vector store is unavailable")

    monkeypatch.setattr("services.course.get_vector_store", lambda: FailingStore())
    failed = authz_api.client.delete(
        f"/api/courses/{authz_api.a_course_id}", headers=authz_api.authorization_a
    )

    assert failed.status_code == 500
    assert failed.json() == {"detail": "Course cleanup failed; retry hard deletion"}
    with authz_api.session_factory() as session:
        course = session.get(Course, authz_api.a_course_id)
        assert course is not None
        assert course.is_deleted is True
        assert session.get(UploadedDocument, authz_api.a_document_id) is not None
        assert store.count_course_vectors(session, authz_api.a_course_id) == 1
    assert _stored_files(authz_api.storage_root) == []

    monkeypatch.setattr("services.course.get_vector_store", lambda: store)
    retried = authz_api.client.delete(
        f"/api/courses/{authz_api.a_course_id}", headers=authz_api.authorization_a
    )

    assert retried.status_code == 200, retried.text
    with authz_api.session_factory() as session:
        assert session.get(Course, authz_api.a_course_id) is None
        assert store.count_course_vectors(session, authz_api.a_course_id) == 0
        assert session.get(ProfileKnowledge, graph["knowledge_id"]) is not None


def test_a_refund_conflict_tombstones_before_course_cleanup(
    authz_api, graph, monkeypatch: pytest.MonkeyPatch
) -> None:
    with authz_api.session_factory() as session:
        job = enqueue_generation_job(
            session,
            course_id=authz_api.a_course_id,
            user_id=authz_api.user_a_id,
            job_type="generate_study_guide",
            request_payload='{"topic_focus":"Atomicity"}',
            credit_cost=1.0,
        )
        job_id = job.id
        charge_transaction_id = job.charge_transaction_id
    stored_before = _stored_files(authz_api.storage_root)
    original_record = CreditService._record

    def reject_refund(db, **values):
        if values.get("refunds_transaction_id") is not None:
            from sqlalchemy.exc import IntegrityError

            raise IntegrityError("refund conflict", {}, RuntimeError("duplicate"))
        return original_record(db, **values)

    monkeypatch.setattr(CreditService, "_record", staticmethod(reject_refund))

    failed = authz_api.client.delete(
        f"/api/courses/{authz_api.a_course_id}", headers=authz_api.authorization_a
    )

    assert failed.status_code == 500
    assert failed.json() == {"detail": "Course cleanup failed; retry hard deletion"}
    assert _stored_files(authz_api.storage_root) == stored_before
    with authz_api.session_factory() as session:
        course = session.get(Course, authz_api.a_course_id)
        job = session.get(GenerationJob, job_id)
        assert course is not None
        assert course.is_deleted is True
        assert job is not None
        assert job.status == "failed"
        assert job.charge_refunded is False
        assert (
            session.scalars(
                select(CreditTransaction).where(
                    CreditTransaction.reason == CreditReason.GENERATION_REFUND.value,
                    CreditTransaction.refunds_transaction_id == charge_transaction_id,
                )
            ).all()
            == []
        )
