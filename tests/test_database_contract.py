from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import DateTime, Engine, Text, delete, inspect, select
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.models import (
    AiUsageLog,
    Course,
    DocumentChunk,
    DocumentPage,
    DocumentVisual,
    GeneratedOutput,
    JOB_STATUS_FAILED,
    ProcessingJob,
    ProfileKnowledge,
    Progress,
    Quiz,
    QuizAttempt,
    QuizQuestion,
    Role,
    UploadedDocument,
    User,
)
from services.processing_jobs import (
    claim_next_job,
    enqueue_document_job,
    fail_job,
)

pytestmark = pytest.mark.database_contract


def test_unloaded_user_delete_cascades_complete_relational_graph(
    session_factory: sessionmaker[Session],
) -> None:
    supplied_time = datetime(
        2026,
        8,
        14,
        20,
        30,
        tzinfo=timezone(timedelta(hours=3)),
    )
    expected_time = supplied_time.astimezone(timezone.utc)
    document_id = uuid4()

    with session_factory() as session:
        role = session.scalar(select(Role).where(Role.name == "user"))
        assert role is not None
        user = User(
            name="Contract user",
            email="contract-cascade@example.com",
            password_hash="not-a-real-hash",
            role=role,
        )
        course = Course(
            owner=user,
            title="Contract course",
            semester="Fall",
            exam_date="2026",
        )
        document = UploadedDocument(
            id=document_id,
            original_file_name="contract.txt",
            file_type="txt",
            mime_type="text/plain",
            file_size=8,
            file_hash="f" * 64,
            uploader=user,
            course=course,
            storage_provider="local:contract",
            storage_key=f"contract/{document_id}.txt",
        )
        page = DocumentPage(
            document=document,
            course=course,
            content_index=0,
            page_number=1,
            raw_text="Raw contract",
            text="Contract text",
            raw_extraction_method="native",
            extraction_method="native",
            has_images=True,
            needs_ocr=False,
            has_visual_content=True,
            visual_analysis_status="completed",
        )
        visual = DocumentVisual(
            page=page,
            visual_index=0,
            visual_type="diagram",
            source="image",
            bbox_x0=1,
            bbox_y0=2,
            bbox_x1=10,
            bbox_y1=20,
            description="Contract diagram",
            analysis_status="succeeded",
        )
        chunk = DocumentChunk(
            document=document,
            course=course,
            chunk_index=0,
            page_number=1,
            end_page_number=1,
            text="Contract text",
        )
        generated_output = GeneratedOutput(
            course=course,
            output_type="summary",
            content="Contract summary",
        )
        quiz = Quiz(course=course, title="Contract quiz")
        question = QuizQuestion(
            quiz=quiz,
            question_index=0,
            question_text="Contract question?",
            options=["Yes", "No"],
            correct_option_index=0,
        )
        attempt = QuizAttempt(user=user, quiz=quiz, score=1)
        progress = Progress(user=user, course=course, completion=0.5)
        knowledge = ProfileKnowledge(
            user=user,
            topic="contracts",
            detail="Understands relational contracts.",
        )
        usage_log = AiUsageLog(
            user=user,
            course=course,
            generation_type="study_guide",
            provider="gemini",
            model="gemini-2.5-flash",
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            latency_ms=150,
            success=True,
        )
        session.add_all(
            (
                document,
                page,
                visual,
                chunk,
                generated_output,
                question,
                attempt,
                progress,
                knowledge,
                usage_log,
            )
        )
        session.flush()
        job = enqueue_document_job(session, document, now=supplied_time)
        session.commit()
        user_id = user.id
        course_id = course.id
        page_id = page.id
        visual_id = visual.id
        chunk_id = chunk.id
        job_id = job.id
        generated_output_id = generated_output.id
        quiz_id = quiz.id
        question_id = question.id
        attempt_id = attempt.id
        progress_id = progress.id
        knowledge_id = knowledge.id
        usage_log_id = usage_log.id

    with session_factory() as session:
        persisted_document = session.get(UploadedDocument, document_id)
        persisted_job = session.get(ProcessingJob, job_id)
        assert persisted_document is not None
        assert persisted_job is not None
        assert isinstance(persisted_document.id, UUID)
        assert persisted_job.available_at == expected_time
        assert persisted_job.available_at.utcoffset() == timedelta(0)
        timestamped_rows = (
            session.get(User, user_id),
            session.get(Course, course_id),
            persisted_document,
            session.get(DocumentPage, page_id),
            session.get(DocumentVisual, visual_id),
            session.get(DocumentChunk, chunk_id),
            session.get(GeneratedOutput, generated_output_id),
            session.get(Quiz, quiz_id),
            session.get(QuizAttempt, attempt_id),
            session.get(Progress, progress_id),
            session.get(ProfileKnowledge, knowledge_id),
        )
        for row in timestamped_rows:
            assert row is not None
            timestamp = row.updated_at if isinstance(row, Progress) else row.created_at
            assert timestamp.utcoffset() == timedelta(0)

    with session_factory() as session:
        session.execute(delete(User).where(User.id == user_id))
        session.commit()

    with session_factory() as session:
        assert session.get(User, user_id) is None
        assert session.get(Course, course_id) is None
        assert session.get(UploadedDocument, document_id) is None
        assert session.get(DocumentPage, page_id) is None
        assert session.get(DocumentVisual, visual_id) is None
        assert session.get(DocumentChunk, chunk_id) is None
        assert session.get(ProcessingJob, job_id) is None
        assert session.get(GeneratedOutput, generated_output_id) is None
        assert session.get(Quiz, quiz_id) is None
        assert session.get(QuizQuestion, question_id) is None
        assert session.get(QuizAttempt, attempt_id) is None
        assert session.get(Progress, progress_id) is None
        assert session.get(ProfileKnowledge, knowledge_id) is None
        assert session.get(AiUsageLog, usage_log_id) is None
        assert session.scalar(select(Role.id).where(Role.name == "user")) is not None


def test_unloaded_course_delete_cascades_every_course_branch(
    session_factory: sessionmaker[Session],
) -> None:
    document_id = uuid4()
    with session_factory() as session:
        role = session.scalar(select(Role).where(Role.name == "user"))
        assert role is not None
        user = User(
            name="Course contract user",
            email="contract-course@example.com",
            password_hash="not-a-real-hash",
            role=role,
        )
        course = Course(
            owner=user,
            title="Course cascade contract",
            semester="Fall",
            exam_date="2026",
        )
        document = UploadedDocument(
            id=document_id,
            original_file_name="course-contract.txt",
            file_type="txt",
            mime_type="text/plain",
            file_size=8,
            file_hash="e" * 64,
            uploader=user,
            course=course,
            storage_provider="local:contract",
            storage_key=f"contract/{document_id}.txt",
        )
        generated_output = GeneratedOutput(
            course=course,
            output_type="summary",
            content="Course contract summary",
        )
        quiz = Quiz(course=course, title="Course contract quiz")
        question = QuizQuestion(
            quiz=quiz,
            question_index=0,
            question_text="Course contract question?",
            options=["Yes", "No"],
            correct_option_index=0,
        )
        attempt = QuizAttempt(user=user, quiz=quiz, score=0.5)
        progress = Progress(user=user, course=course, completion=0.5)
        knowledge = ProfileKnowledge(
            user=user,
            topic="course cascades",
            detail="Must survive course deletion.",
        )
        usage_log = AiUsageLog(
            user=user,
            course=course,
            generation_type="quiz",
            provider="gemini",
            model="gemini-2.5-flash",
            success=True,
        )
        session.add_all(
            (
                document,
                generated_output,
                question,
                attempt,
                progress,
                knowledge,
                usage_log,
            )
        )
        session.flush()
        job = enqueue_document_job(session, document)
        session.commit()
        user_id = user.id
        course_id = course.id
        generated_output_id = generated_output.id
        quiz_id = quiz.id
        question_id = question.id
        attempt_id = attempt.id
        progress_id = progress.id
        knowledge_id = knowledge.id
        job_id = job.id
        usage_log_id = usage_log.id

    with session_factory() as session:
        session.execute(delete(Course).where(Course.id == course_id))
        session.commit()

    with session_factory() as session:
        assert session.get(Course, course_id) is None
        assert session.get(UploadedDocument, document_id) is None
        assert session.get(ProcessingJob, job_id) is None
        assert session.get(GeneratedOutput, generated_output_id) is None
        assert session.get(Quiz, quiz_id) is None
        assert session.get(QuizQuestion, question_id) is None
        assert session.get(QuizAttempt, attempt_id) is None
        assert session.get(Progress, progress_id) is None
        assert session.get(AiUsageLog, usage_log_id) is None
        assert session.get(User, user_id) is not None
        assert session.get(ProfileKnowledge, knowledge_id) is not None


def test_referenced_role_delete_is_restricted(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        role = session.scalar(select(Role).where(Role.name == "user"))
        assert role is not None
        user = User(
            name="Role contract user",
            email="contract-role@example.com",
            password_hash="not-a-real-hash",
            role=role,
        )
        session.add(user)
        session.commit()
        user_id = user.id
        role_id = role.id

    with session_factory() as session:
        with pytest.raises(IntegrityError):
            session.execute(delete(Role).where(Role.id == role_id))
            session.commit()
        session.rollback()

        assert session.get(Role, role_id) is not None
        assert session.get(User, user_id) is not None


@pytest.mark.parametrize(
    "overrides",
    [{"question_index": -1}, {"correct_option_index": -1}],
    ids=["question-index", "correct-option-index"],
)
def test_quiz_question_indexes_are_nonnegative(
    session_factory: sessionmaker[Session],
    overrides: dict[str, int],
) -> None:
    with session_factory() as session:
        role = session.scalar(select(Role).where(Role.name == "user"))
        assert role is not None
        user = User(
            name="Quiz constraint user",
            email="quiz-constraint@example.com",
            password_hash="not-a-real-hash",
            role=role,
        )
        course = Course(
            owner=user,
            title="Quiz constraint course",
        )
        quiz = Quiz(course=course, title="Constraint quiz")
        values = {
            "quiz": quiz,
            "question_index": 0,
            "question_text": "Question?",
            "options": ["Yes", "No"],
            "correct_option_index": 0,
        }
        values.update(overrides)
        session.add(QuizQuestion(**values))

        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_processing_job_identifiers_and_ownership_are_enforced(
    session_factory: sessionmaker[Session],
) -> None:
    queued_at = datetime.now(timezone.utc)
    document_id = uuid4()
    with session_factory() as session:
        role = session.scalar(select(Role).where(Role.name == "user"))
        assert role is not None
        user = User(
            name="Job constraint user",
            email="job-constraint@example.com",
            password_hash="not-a-real-hash",
            role=role,
        )
        course = Course(
            owner=user,
            title="Job constraint course",
        )
        other_course = Course(
            owner=user,
            title="Other job course",
        )
        document = UploadedDocument(
            id=document_id,
            original_file_name="job-contract.txt",
            file_type="txt",
            mime_type="text/plain",
            file_size=8,
            file_hash="d" * 64,
            uploader=user,
            course=course,
            storage_provider="local:contract",
            storage_key=f"contract/{document_id}.txt",
        )
        session.add_all((document, other_course))
        session.flush()
        job = enqueue_document_job(session, document, now=queued_at)
        session.commit()
        job_id = job.id
        other_course_id = other_course.id

    for field, value in (("job_type", "unknown"), ("course_id", other_course_id)):
        with session_factory() as session:
            job = session.get(ProcessingJob, job_id)
            assert job is not None
            setattr(job, field, value)
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

    with session_factory() as session:
        claim = claim_next_job(
            session,
            "contract-worker",
            "local:contract",
            60,
            now=queued_at + timedelta(seconds=1),
        )
    assert claim is not None

    for field, value in (
        ("lease_owner", "   "),
        ("lease_owner", "\t\n"),
        ("claim_token", "short"),
    ):
        with session_factory() as session:
            job = session.get(ProcessingJob, job_id)
            assert job is not None
            setattr(job, field, value)
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

    with session_factory() as session:
        assert (
            fail_job(
                session,
                job_id,
                claim.claim_token,
                error_code="CONTRACT_FAILURE",
                error_message="Contract failure",
                retryable=False,
                now=queued_at + timedelta(seconds=2),
            )
            == JOB_STATUS_FAILED
        )
    with session_factory() as session:
        job = session.get(ProcessingJob, job_id)
        assert job is not None
        for value in ("   ", "\t\n"):
            job.last_error_code = value
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()
            job = session.get(ProcessingJob, job_id)
            assert job is not None


def test_utc_datetime_rejects_naive_values(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        role = session.scalar(select(Role).where(Role.name == "user"))
        assert role is not None
        session.add(
            User(
                name="Naive timestamp user",
                email="naive-timestamp@example.com",
                password_hash="not-a-real-hash",
                role=role,
                created_at=datetime(2026, 8, 14, 12, 0),
            )
        )
        with pytest.raises(StatementError):
            session.commit()
        session.rollback()


def test_migrated_schema_has_no_column_drift_from_the_models(
    schema_drift: dict[str, dict[str, list[str]]],
) -> None:
    """Alembic and the ORM models must describe the same columns.

    A model column with no migration deploys as a missing column, and a
    migration column with no model is dead schema. Either direction fails here.
    """
    assert schema_drift == {}


def test_course_workspace_columns_are_migrated_as_designed(
    database_engine: Engine,
) -> None:
    """The new columns must carry the nullability the model declares."""
    columns = {
        column["name"]: column
        for column in inspect(database_engine).get_columns("courses")
    }
    assert columns["syllabus"]["nullable"] is True
    assert columns["updated_at"]["nullable"] is False
    assert isinstance(columns["syllabus"]["type"], Text)
    assert isinstance(columns["updated_at"]["type"], DateTime)
