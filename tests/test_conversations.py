"""Course-scoped reads of persisted Course Q&A and AI Tutor conversations."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from backend.app.models import Conversation, ConversationMessage
from services.conversation import (
    CONVERSATION_HISTORY_MAX_CHARS,
    CONVERSATION_HISTORY_OMITTED,
    ConversationService,
)


def _store_conversation(
    session_factory,
    course_id: int,
    user_id: int,
    *,
    conversation_type: str = "course_qa",
    messages: tuple[tuple[str, str, datetime], ...] = (),
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> tuple[int, list[int]]:
    with session_factory() as session:
        conversation = Conversation(
            course_id=course_id,
            user_id=user_id,
            conversation_type=conversation_type,
            created_at=created_at,
            updated_at=updated_at,
        )
        session.add(conversation)
        session.flush()
        stored_messages = [
            ConversationMessage(
                conversation=conversation,
                role=role,
                content=content,
                created_at=message_created_at,
            )
            for role, content, message_created_at in messages
        ]
        session.add_all(stored_messages)
        session.commit()
        return conversation.id, [message.id for message in stored_messages]


def _list_url(course_id: int) -> str:
    return f"/api/courses/{course_id}/conversations"


def _detail_url(course_id: int, conversation_id: int) -> str:
    return f"{_list_url(course_id)}/{conversation_id}"


def _parsed_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_prompt_history_keeps_recent_messages_within_its_budget() -> None:
    conversation = Conversation(course_id=1, user_id=1, conversation_type="course_qa")
    conversation.messages = [
        ConversationMessage(role="user", content="oldest " * 2_000),
        ConversationMessage(role="assistant", content="older answer"),
        ConversationMessage(role="user", content="recent question"),
        ConversationMessage(role="assistant", content="recent answer"),
    ]

    history = ConversationService.format_history(conversation)

    assert len(history) <= CONVERSATION_HISTORY_MAX_CHARS
    assert history.startswith(CONVERSATION_HISTORY_OMITTED)
    assert "oldest" not in history
    assert "User: recent question" in history
    assert "Assistant: recent answer" in history


def test_owner_lists_typed_course_conversations_by_recent_activity(upload_api) -> None:
    started_at = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)
    long_question = "  Explain   the\ncell cycle " + "in detail " * 25
    normalized_question = " ".join(long_question.split())
    older_id, _ = _store_conversation(
        upload_api.session_factory,
        upload_api.course_id,
        upload_api.user_id,
        messages=(
            ("user", "What is mitosis?", started_at),
            (
                "assistant",
                "Mitosis is cell division.",
                started_at + timedelta(seconds=1),
            ),
        ),
        created_at=started_at,
        updated_at=started_at + timedelta(minutes=1),
    )
    first_tied_id, _ = _store_conversation(
        upload_api.session_factory,
        upload_api.course_id,
        upload_api.user_id,
        conversation_type="ai_tutor",
        messages=(
            ("assistant", "Let us begin.", started_at + timedelta(minutes=2)),
            ("user", long_question, started_at + timedelta(minutes=2, seconds=1)),
            (
                "assistant",
                "Start with interphase.",
                started_at + timedelta(minutes=2, seconds=2),
            ),
        ),
        created_at=started_at + timedelta(minutes=2),
        updated_at=started_at + timedelta(minutes=4),
    )
    second_tied_id, _ = _store_conversation(
        upload_api.session_factory,
        upload_api.course_id,
        upload_api.user_id,
        messages=(),
        created_at=started_at + timedelta(minutes=3),
        updated_at=started_at + timedelta(minutes=4),
    )
    _store_conversation(
        upload_api.session_factory,
        upload_api.other_course_id,
        upload_api.user_id,
        messages=(("user", "Do not leak this conversation.", started_at),),
        created_at=started_at,
        updated_at=started_at + timedelta(days=1),
    )

    response = upload_api.client.get(
        _list_url(upload_api.course_id), headers=upload_api.authorization
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["success"] is True
    assert payload["message"] == "Conversations retrieved successfully"
    summaries = payload["data"]
    assert [summary["id"] for summary in summaries] == [
        second_tied_id,
        first_tied_id,
        older_id,
    ]
    assert [summary["conversation_type"] for summary in summaries] == [
        "course_qa",
        "ai_tutor",
        "course_qa",
    ]
    assert summaries[0]["preview"] == ""
    assert summaries[0]["message_count"] == 0
    assert summaries[1]["preview"] == normalized_question[:160]
    assert summaries[1]["message_count"] == 3
    assert summaries[2]["preview"] == "What is mitosis?"
    assert summaries[2]["message_count"] == 2

    expected_fields = {
        "id",
        "course_id",
        "user_id",
        "conversation_type",
        "preview",
        "message_count",
        "created_at",
        "updated_at",
    }
    for summary in summaries:
        assert set(summary) == expected_fields
        assert isinstance(summary["id"], int)
        assert summary["course_id"] == upload_api.course_id
        assert summary["user_id"] == upload_api.user_id
        assert isinstance(summary["preview"], str)
        assert isinstance(summary["message_count"], int)
        assert _parsed_datetime(summary["created_at"]).tzinfo is not None
        assert _parsed_datetime(summary["updated_at"]).tzinfo is not None


def test_conversation_list_is_empty_for_a_course_without_history(upload_api) -> None:
    response = upload_api.client.get(
        _list_url(upload_api.course_id), headers=upload_api.authorization
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"] == []


def test_owner_reads_typed_detail_with_chronological_messages(upload_api) -> None:
    started_at = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
    messages = (
        ("user", "What is ATP?", started_at),
        (
            "assistant",
            "ATP carries cellular energy.",
            started_at + timedelta(seconds=1),
        ),
        ("user", "Where is it produced?", started_at + timedelta(seconds=2)),
        (
            "assistant",
            "Most ATP is produced in mitochondria.",
            started_at + timedelta(seconds=3),
        ),
    )
    conversation_id, message_ids = _store_conversation(
        upload_api.session_factory,
        upload_api.course_id,
        upload_api.user_id,
        conversation_type="ai_tutor",
        messages=messages,
        created_at=started_at,
        updated_at=started_at + timedelta(seconds=3),
    )

    response = upload_api.client.get(
        _detail_url(upload_api.course_id, conversation_id),
        headers=upload_api.authorization,
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["success"] is True
    assert payload["message"] == "Conversation retrieved successfully"
    detail = payload["data"]
    assert detail["id"] == conversation_id
    assert detail["course_id"] == upload_api.course_id
    assert detail["user_id"] == upload_api.user_id
    assert detail["conversation_type"] == "ai_tutor"
    assert detail["preview"] == "What is ATP?"
    assert detail["message_count"] == 4
    assert [message["id"] for message in detail["messages"]] == message_ids
    assert [message["role"] for message in detail["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert [message["content"] for message in detail["messages"]] == [
        content for _role, content, _created_at in messages
    ]
    message_times = [
        _parsed_datetime(message["created_at"]) for message in detail["messages"]
    ]
    assert message_times == sorted(message_times)
    assert all(
        set(message) == {"id", "role", "content", "created_at"}
        for message in detail["messages"]
    )


def test_administrator_may_read_another_owners_conversation(authz_api) -> None:
    started_at = datetime(2026, 8, 20, 11, 0, tzinfo=timezone.utc)
    conversation_id, _ = _store_conversation(
        authz_api.session_factory,
        authz_api.a_course_id,
        authz_api.user_a_id,
        messages=(
            ("user", "Owner A question", started_at),
            ("assistant", "Stored answer", started_at + timedelta(seconds=1)),
        ),
        created_at=started_at,
        updated_at=started_at + timedelta(seconds=1),
    )

    listing = authz_api.client.get(
        _list_url(authz_api.a_course_id), headers=authz_api.authorization_admin
    )
    detail = authz_api.client.get(
        _detail_url(authz_api.a_course_id, conversation_id),
        headers=authz_api.authorization_admin,
    )

    assert listing.status_code == 200, listing.text
    assert [entry["id"] for entry in listing.json()["data"]] == [conversation_id]
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["messages"][0]["content"] == "Owner A question"


def test_another_user_cannot_reach_course_conversations(authz_api) -> None:
    started_at = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    conversation_id, _ = _store_conversation(
        authz_api.session_factory,
        authz_api.a_course_id,
        authz_api.user_a_id,
        messages=(("user", "Private question", started_at),),
        created_at=started_at,
        updated_at=started_at,
    )

    listing = authz_api.client.get(
        _list_url(authz_api.a_course_id), headers=authz_api.authorization_b
    )
    detail = authz_api.client.get(
        _detail_url(authz_api.a_course_id, conversation_id),
        headers=authz_api.authorization_b,
    )

    assert listing.status_code == 404
    assert listing.json() == {"detail": "Course not found"}
    assert detail.status_code == 404
    assert detail.json() == {"detail": "Course not found"}


def test_missing_and_cross_course_conversations_are_not_found(upload_api) -> None:
    started_at = datetime(2026, 8, 20, 13, 0, tzinfo=timezone.utc)
    other_conversation_id, _ = _store_conversation(
        upload_api.session_factory,
        upload_api.other_course_id,
        upload_api.user_id,
        messages=(("user", "Question for another course", started_at),),
        created_at=started_at,
        updated_at=started_at,
    )

    missing = upload_api.client.get(
        _detail_url(upload_api.course_id, 999999), headers=upload_api.authorization
    )
    cross_course = upload_api.client.get(
        _detail_url(upload_api.course_id, other_conversation_id),
        headers=upload_api.authorization,
    )

    assert missing.status_code == 404
    assert missing.json() == {"detail": "Conversation not found"}
    assert cross_course.status_code == 404
    assert cross_course.json() == {"detail": "Conversation not found"}


def test_missing_and_tombstoned_courses_hide_conversations(upload_api) -> None:
    started_at = datetime(2026, 8, 20, 14, 0, tzinfo=timezone.utc)
    conversation_id, _ = _store_conversation(
        upload_api.session_factory,
        upload_api.deleted_course_id,
        upload_api.user_id,
        messages=(("user", "Deleted course question", started_at),),
        created_at=started_at,
        updated_at=started_at,
    )

    responses = (
        upload_api.client.get(_list_url(999999), headers=upload_api.authorization),
        upload_api.client.get(_detail_url(999999, 1), headers=upload_api.authorization),
        upload_api.client.get(
            _list_url(upload_api.deleted_course_id), headers=upload_api.authorization
        ),
        upload_api.client.get(
            _detail_url(upload_api.deleted_course_id, conversation_id),
            headers=upload_api.authorization,
        ),
    )

    for response in responses:
        assert response.status_code == 404
        assert response.json() == {"detail": "Course not found"}


def test_conversation_routes_require_authentication(api_context) -> None:
    listing = api_context.client.get(_list_url(1))
    detail = api_context.client.get(_detail_url(1, 1))

    assert listing.status_code == 401
    assert detail.status_code == 401


def test_reading_conversations_never_invokes_a_provider_or_changes_rows(
    upload_api, monkeypatch
) -> None:
    import routes.ai_tutor as ai_tutor_route
    import routes.course_qa as course_qa_route
    import services.text_generation as text_generation_service

    def forbidden(*_args, **_kwargs):
        raise AssertionError("reading conversation history must not call a provider")

    monkeypatch.setattr(ai_tutor_route, "get_text_generation_provider", forbidden)
    monkeypatch.setattr(course_qa_route, "get_text_generation_provider", forbidden)
    monkeypatch.setattr(
        text_generation_service, "get_text_generation_provider", forbidden
    )
    started_at = datetime(2026, 8, 20, 15, 0, tzinfo=timezone.utc)
    conversation_id, _ = _store_conversation(
        upload_api.session_factory,
        upload_api.course_id,
        upload_api.user_id,
        messages=(
            ("user", "Read this from storage", started_at),
            ("assistant", "Stored response", started_at + timedelta(seconds=1)),
        ),
        created_at=started_at,
        updated_at=started_at + timedelta(seconds=1),
    )
    with upload_api.session_factory() as session:
        before = (
            session.scalar(select(func.count()).select_from(Conversation)),
            session.scalar(select(func.count()).select_from(ConversationMessage)),
        )

    listing = upload_api.client.get(
        _list_url(upload_api.course_id), headers=upload_api.authorization
    )
    detail = upload_api.client.get(
        _detail_url(upload_api.course_id, conversation_id),
        headers=upload_api.authorization,
    )

    assert listing.status_code == 200, listing.text
    assert detail.status_code == 200, detail.text
    with upload_api.session_factory() as session:
        after = (
            session.scalar(select(func.count()).select_from(Conversation)),
            session.scalar(select(func.count()).select_from(ConversationMessage)),
        )
    assert after == before


def test_owner_deletes_conversation_and_its_messages(upload_api) -> None:
    started_at = datetime(2026, 8, 20, 16, 0, tzinfo=timezone.utc)
    conversation_id, message_ids = _store_conversation(
        upload_api.session_factory,
        upload_api.course_id,
        upload_api.user_id,
        messages=(
            ("user", "Delete this question", started_at),
            ("assistant", "Delete this answer", started_at + timedelta(seconds=1)),
        ),
    )

    with upload_api.session_factory() as session:
        assert session.get(Conversation, conversation_id) is not None
        for message_id in message_ids:
            assert session.get(ConversationMessage, message_id) is not None

    response = upload_api.client.delete(
        _detail_url(upload_api.course_id, conversation_id),
        headers=upload_api.authorization,
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["success"] is True
    assert payload["message"] == "Conversation deleted successfully"
    assert payload["data"] == {"id": conversation_id}

    with upload_api.session_factory() as session:
        assert session.get(Conversation, conversation_id) is None
        for message_id in message_ids:
            assert session.get(ConversationMessage, message_id) is None


def test_cross_user_cannot_delete_conversation(authz_api) -> None:
    started_at = datetime(2026, 8, 20, 17, 0, tzinfo=timezone.utc)
    conversation_id, message_ids = _store_conversation(
        authz_api.session_factory,
        authz_api.a_course_id,
        authz_api.user_a_id,
        messages=(("user", "Protected question", started_at),),
    )

    response = authz_api.client.delete(
        _detail_url(authz_api.a_course_id, conversation_id),
        headers=authz_api.authorization_b,
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Course not found"}

    with authz_api.session_factory() as session:
        assert session.get(Conversation, conversation_id) is not None
        assert session.get(ConversationMessage, message_ids[0]) is not None


def test_administrator_cannot_delete_another_owners_conversation(authz_api) -> None:
    started_at = datetime(2026, 8, 20, 18, 0, tzinfo=timezone.utc)
    conversation_id, message_ids = _store_conversation(
        authz_api.session_factory,
        authz_api.a_course_id,
        authz_api.user_a_id,
        messages=(("user", "Admin cannot delete", started_at),),
    )

    response = authz_api.client.delete(
        _detail_url(authz_api.a_course_id, conversation_id),
        headers=authz_api.authorization_admin,
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Course not found"}

    with authz_api.session_factory() as session:
        assert session.get(Conversation, conversation_id) is not None
        assert session.get(ConversationMessage, message_ids[0]) is not None


def test_cross_course_conversation_delete_is_not_found(upload_api) -> None:
    started_at = datetime(2026, 8, 20, 19, 0, tzinfo=timezone.utc)
    other_conversation_id, _ = _store_conversation(
        upload_api.session_factory,
        upload_api.other_course_id,
        upload_api.user_id,
        messages=(("user", "Question in other course", started_at),),
    )

    response = upload_api.client.delete(
        _detail_url(upload_api.course_id, other_conversation_id),
        headers=upload_api.authorization,
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Conversation not found"}

    with upload_api.session_factory() as session:
        assert session.get(Conversation, other_conversation_id) is not None


def test_delete_nonexistent_and_tombstoned_course_conversations(upload_api) -> None:
    started_at = datetime(2026, 8, 20, 20, 0, tzinfo=timezone.utc)
    conversation_id, _ = _store_conversation(
        upload_api.session_factory,
        upload_api.deleted_course_id,
        upload_api.user_id,
        messages=(("user", "Tombstoned course", started_at),),
    )

    res_missing_convo = upload_api.client.delete(
        _detail_url(upload_api.course_id, 999999),
        headers=upload_api.authorization,
    )
    assert res_missing_convo.status_code == 404
    assert res_missing_convo.json() == {"detail": "Conversation not found"}

    res_tombstoned = upload_api.client.delete(
        _detail_url(upload_api.deleted_course_id, conversation_id),
        headers=upload_api.authorization,
    )
    assert res_tombstoned.status_code == 404
    assert res_tombstoned.json() == {"detail": "Course not found"}

    res_unauth = upload_api.client.delete(
        _detail_url(upload_api.course_id, conversation_id),
    )
    assert res_unauth.status_code == 401
