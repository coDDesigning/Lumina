import logging
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

import services.ai_usage_logger as ai_usage_logger
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


def test_log_success_persists_the_versioned_cost_estimate(
    monkeypatch,
    session_factory: sessionmaker[Session],
) -> None:
    monkeypatch.setattr(
        ai_usage_logger,
        "settings",
        SimpleNamespace(
            ai_pricing_version="2026-08-24",
            ai_model_cost_rates={
                "gemini:gemini-2.5-flash": {
                    "prompt_usd_per_million_tokens": 1.0,
                    "completion_usd_per_million_tokens": 2.0,
                }
            },
        ),
    )
    with session_factory() as session:
        user, _ = _create_user_and_course(session)
        log = AiUsageLogger.log_success(
            session,
            user_id=user.id,
            generation_type=GenerationType.STUDY_GUIDE,
            metadata=GenerationMetadata(
                provider="gemini",
                model="gemini-2.5-flash",
                prompt_tokens=100_000,
                completion_tokens=200_000,
            ),
        )
        session.commit()

        assert log is not None
        assert log.estimated_cost_usd == 0.5
        assert log.pricing_version == "2026-08-24"


def test_nonfinite_calculated_cost_is_left_unpriced(
    monkeypatch,
    session_factory: sessionmaker[Session],
) -> None:
    monkeypatch.setattr(
        ai_usage_logger,
        "settings",
        SimpleNamespace(
            ai_pricing_version="overflow-test",
            ai_model_cost_rates={
                "gemini:model": {
                    "prompt_usd_per_million_tokens": 1e308,
                    "completion_usd_per_million_tokens": 1e308,
                }
            },
        ),
    )
    with session_factory() as session:
        user, _ = _create_user_and_course(session)
        log = AiUsageLogger.log_success(
            session,
            user_id=user.id,
            generation_type=GenerationType.QUIZ,
            metadata=GenerationMetadata(
                provider="gemini",
                model="model",
                prompt_tokens=100,
                completion_tokens=100,
            ),
        )

        assert log is not None
        assert log.estimated_cost_usd is None
        assert log.pricing_version is None


def test_failed_event_with_token_counts_is_left_unpriced(
    monkeypatch,
    session_factory: sessionmaker[Session],
) -> None:
    monkeypatch.setattr(
        ai_usage_logger,
        "settings",
        SimpleNamespace(
            ai_pricing_version="2026-08-24",
            ai_model_cost_rates={
                "gemini:model": {
                    "prompt_usd_per_million_tokens": 1.0,
                    "completion_usd_per_million_tokens": 2.0,
                }
            },
        ),
    )
    with session_factory() as session:
        user, _ = _create_user_and_course(session)
        log = AiUsageLogger.log_usage(
            session,
            user_id=user.id,
            generation_type=GenerationType.QUIZ,
            provider="gemini",
            model="model",
            prompt_tokens=100_000,
            completion_tokens=200_000,
            success=False,
            error_category=ErrorCategory.PROVIDER_ERROR,
        )

        assert log is not None
        assert log.estimated_cost_usd is None
        assert log.pricing_version is None


def test_log_failure_persists_categorical_error(
    session_factory: sessionmaker[Session],
) -> None:
    """Called without metadata, so the token counts stay empty by construction."""
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
        assert session.scalar(select(User.id).where(User.id == user.id)) == user.id


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


def test_failure_telemetry_records_the_configured_provider(
    monkeypatch,
    session_factory: sessionmaker[Session],
) -> None:
    monkeypatch.setattr(
        ai_usage_logger,
        "configured_provider_identity",
        lambda: ("ollama", "qwen3:8b"),
    )

    with session_factory() as session:
        user, course = _create_user_and_course(session)

        log = AiUsageLogger.log_failure(
            session,
            user_id=user.id,
            course_id=course.id,
            generation_type=GenerationType.STUDY_GUIDE,
            error_category=ErrorCategory.PROVIDER_ERROR,
        )
        session.commit()

        assert log is not None
        assert log.provider == "ollama"
        assert log.model == "qwen3:8b"


def test_explicit_provider_overrides_configured_identity(
    monkeypatch,
    session_factory: sessionmaker[Session],
) -> None:
    monkeypatch.setattr(
        ai_usage_logger,
        "configured_provider_identity",
        lambda: ("ollama", "qwen3:8b"),
    )

    with session_factory() as session:
        user, _ = _create_user_and_course(session)

        log = AiUsageLogger.log_usage(
            session,
            user_id=user.id,
            generation_type=GenerationType.QUIZ,
            provider="gemini",
            model="gemini-2.5-flash",
        )
        session.commit()

        assert log is not None
        assert log.provider == "gemini"
        assert log.model == "gemini-2.5-flash"


def test_log_failure_unpacks_metadata_like_log_success(
    session_factory: sessionmaker[Session],
) -> None:
    """SCRUM-206: a billed generation that then failed still reports its tokens."""
    with session_factory() as session:
        user, course = _create_user_and_course(session)

        log = AiUsageLogger.log_failure(
            session,
            user_id=user.id,
            course_id=course.id,
            generation_type=GenerationType.QUIZ,
            error_category=ErrorCategory.INVALID_STRUCTURE,
            metadata=GenerationMetadata(
                provider="ollama",
                model="llama3",
                prompt_tokens=42,
                completion_tokens=84,
                total_tokens=126,
                latency_ms=250,
            ),
        )
        session.commit()

        assert log is not None
        log_id = log.id

    with session_factory() as session:
        persisted = session.get(AiUsageLog, log_id)
        assert persisted is not None
        assert persisted.provider == "ollama"
        assert persisted.model == "llama3"
        assert persisted.prompt_tokens == 42
        assert persisted.completion_tokens == 84
        assert persisted.total_tokens == 126
        assert persisted.latency_ms == 250
        assert persisted.success is False


def test_a_failure_without_a_user_is_logged_but_not_persisted(
    session_factory: sessionmaker[Session],
    caplog,
) -> None:
    """SCRUM-206: an anonymous failure is still diagnosable; only the row needs a user."""
    with session_factory() as session:
        rows_before = len(session.scalars(select(AiUsageLog)).all())

    with caplog.at_level(logging.WARNING, logger="services.ai_usage_logger"):
        with session_factory() as session:
            result = AiUsageLogger.log_failure(
                session,
                user_id=None,
                generation_type=GenerationType.PROMPT_GENERATOR,
                error_category=ErrorCategory.INVALID_STRUCTURE,
                response={"unexpected": 1},
            )
            session.commit()

    assert result is None
    failures = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "ai_generation_failed"
    ]
    assert len(failures) == 1
    assert failures[0].generation_type == "prompt_generator"
    assert failures[0].ai_response_keys == ["unexpected"]
    assert not any(
        "Skipping AI usage log" in record.getMessage() for record in caplog.records
    )

    with session_factory() as session:
        assert len(session.scalars(select(AiUsageLog)).all()) == rows_before


def test_a_failure_without_a_session_is_still_logged(caplog) -> None:
    """SCRUM-206: prompt generation runs with db=None and must not lose its failures."""
    with caplog.at_level(logging.WARNING, logger="services.ai_usage_logger"):
        result = AiUsageLogger.log_failure(
            None,
            user_id=7,
            generation_type=GenerationType.PROMPT_GENERATOR,
            error_category=ErrorCategory.PROVIDER_ERROR,
        )

    assert result is None
    assert any(
        getattr(record, "event", None) == "ai_generation_failed"
        for record in caplog.records
    )
