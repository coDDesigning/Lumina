from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.models import AiUsageLog, Course, Role, User
from schemas.ai_usage import ErrorCategory, GenerationType
from services.ai_usage_logger import AiUsageLogger
from services.text_generation import GenerationMetadata


def _create_user_and_course(session: Session) -> tuple[User, Course]:
    role = session.scalar(select(Role).where(Role.name == "user"))
    if not role:
        role = Role(name="user")
        session.add(role)
        session.flush()

    user = User(
        name="Telemetry Test User",
        email="telemetry@example.com",
        password_hash="test-hash",
        role=role,
    )
    session.add(user)
    session.flush()

    course = Course(
        owner=user,
        title="Telemetry Course",
        semester="Fall 2026",
    )
    session.add(course)
    session.flush()
    return user, course


def test_log_success_persists_structured_event(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        user, course = _create_user_and_course(session)
        metadata = GenerationMetadata(
            provider="gemini",
            model="gemini-2.5-flash",
            prompt_tokens=150,
            completion_tokens=350,
            total_tokens=500,
            latency_ms=1230,
        )

        log = AiUsageLogger.log_success(
            session,
            user_id=user.id,
            course_id=course.id,
            generation_type=GenerationType.STUDY_GUIDE,
            metadata=metadata,
        )
        session.commit()

        assert log is not None
        log_id = log.id

    with session_factory() as session:
        persisted = session.get(AiUsageLog, log_id)
        assert persisted is not None
        assert persisted.user_id == user.id
        assert persisted.course_id == course.id
        assert persisted.generation_type == "study_guide"
        assert persisted.provider == "gemini"
        assert persisted.model == "gemini-2.5-flash"
        assert persisted.prompt_tokens == 150
        assert persisted.completion_tokens == 350
        assert persisted.total_tokens == 500
        assert persisted.latency_ms == 1230
        assert persisted.success is True
        assert persisted.error_category is None
        assert persisted.created_at is not None


def test_log_failure_persists_categorical_error(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        user, course = _create_user_and_course(session)

        log = AiUsageLogger.log_failure(
            session,
            user_id=user.id,
            course_id=course.id,
            generation_type=GenerationType.QUIZ,
            error_category=ErrorCategory.NO_READY_MATERIAL,
            latency_ms=45,
        )
        session.commit()

        assert log is not None
        log_id = log.id

    with session_factory() as session:
        persisted = session.get(AiUsageLog, log_id)
        assert persisted is not None
        assert persisted.user_id == user.id
        assert persisted.course_id == course.id
        assert persisted.generation_type == "quiz"
        assert persisted.success is False
        assert persisted.error_category == "no_ready_material"
        assert persisted.prompt_tokens is None
        assert persisted.completion_tokens is None
        assert persisted.total_tokens is None
        assert persisted.latency_ms == 45


def test_logger_is_resilient_to_database_errors(
    monkeypatch,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        user, course = _create_user_and_course(session)

        def faulty_add(_):
            raise RuntimeError("Simulated database failure")

        monkeypatch.setattr(session, "add", faulty_add)

        # Logging failure should return None and not raise exception
        result = AiUsageLogger.log_usage(
            session,
            user_id=user.id,
            course_id=course.id,
            generation_type="study_guide",
            success=True,
        )
        assert result is None


def test_logger_skips_when_user_id_missing(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        result = AiUsageLogger.log_usage(
            session,
            user_id=0,
            generation_type="prompt_generator",
        )
        assert result is None
