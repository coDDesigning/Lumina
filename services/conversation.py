"""Typed, course-scoped conversation persistence and history reads."""

from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from backend.app.models import Conversation, ConversationMessage
from schemas.conversation import (
    ConversationDetail,
    ConversationMessageResponse,
    ConversationSummary,
    ConversationType,
)
from utils.exceptions import NotFoundException

CONVERSATION_NOT_FOUND = "Conversation not found"
CONVERSATION_PREVIEW_MAX_CHARS = 160
CONVERSATION_HISTORY_MAX_CHARS = 12_000
CONVERSATION_HISTORY_OMITTED = "[Earlier conversation omitted]\n"


class ConversationService:
    @staticmethod
    def get_for_append(
        db: Session,
        conversation_id: int,
        *,
        user_id: int,
        course_id: int,
        conversation_type: ConversationType,
    ) -> Conversation:
        conversation = db.scalar(
            select(Conversation)
            .options(selectinload(Conversation.messages))
            .where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
                Conversation.course_id == course_id,
                Conversation.conversation_type == conversation_type.value,
            )
        )
        if conversation is None:
            raise NotFoundException(CONVERSATION_NOT_FOUND)
        return conversation

    @staticmethod
    def format_history(conversation: Conversation | None) -> str:
        if conversation is None:
            return ""
        history = "\n".join(
            f"{'User' if message.role == 'user' else 'Assistant'}: {message.content}"
            for message in conversation.messages
        )
        if len(history) <= CONVERSATION_HISTORY_MAX_CHARS:
            return history

        tail = history[
            -(CONVERSATION_HISTORY_MAX_CHARS - len(CONVERSATION_HISTORY_OMITTED)) :
        ]
        _, separator, complete_lines = tail.partition("\n")
        return CONVERSATION_HISTORY_OMITTED + (complete_lines if separator else tail)

    @staticmethod
    def record_exchange(
        db: Session,
        *,
        conversation: Conversation | None,
        user_id: int,
        course_id: int,
        conversation_type: ConversationType,
        question: str,
        answer: str,
    ) -> Conversation:
        if conversation is None:
            conversation = Conversation(
                user_id=user_id,
                course_id=course_id,
                conversation_type=conversation_type.value,
            )
            db.add(conversation)
            db.flush()
        else:
            conversation.updated_at = datetime.now(timezone.utc)

        db.add_all(
            [
                ConversationMessage(
                    conversation=conversation,
                    role="user",
                    content=question,
                ),
                ConversationMessage(
                    conversation=conversation,
                    role="assistant",
                    content=answer,
                ),
            ]
        )
        db.flush()
        return conversation

    @staticmethod
    def list_course_conversations(
        db: Session,
        course_id: int,
    ) -> Sequence[ConversationSummary]:
        first_user_message = (
            select(ConversationMessage.content)
            .where(
                ConversationMessage.conversation_id == Conversation.id,
                ConversationMessage.role == "user",
            )
            .order_by(ConversationMessage.id)
            .limit(1)
            .correlate(Conversation)
            .scalar_subquery()
        )
        message_count = (
            select(func.count(ConversationMessage.id))
            .where(ConversationMessage.conversation_id == Conversation.id)
            .correlate(Conversation)
            .scalar_subquery()
        )
        rows = db.execute(
            select(
                Conversation,
                first_user_message.label("preview"),
                message_count.label("message_count"),
            )
            .where(Conversation.course_id == course_id)
            .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        ).all()

        return [
            ConversationService._summary(
                conversation,
                preview=preview,
                message_count=message_count,
            )
            for conversation, preview, message_count in rows
        ]

    @staticmethod
    def get_course_conversation(
        db: Session,
        course_id: int,
        conversation_id: int,
    ) -> Conversation:
        conversation = db.scalar(
            select(Conversation)
            .options(selectinload(Conversation.messages))
            .where(
                Conversation.id == conversation_id,
                Conversation.course_id == course_id,
            )
        )
        if conversation is None:
            raise NotFoundException(CONVERSATION_NOT_FOUND)
        return conversation

    @staticmethod
    def _summary(
        conversation: Conversation,
        *,
        preview: str | None,
        message_count: int,
    ) -> ConversationSummary:
        normalized_preview = " ".join((preview or "").split())
        return ConversationSummary(
            id=conversation.id,
            course_id=conversation.course_id,
            user_id=conversation.user_id,
            conversation_type=ConversationType(conversation.conversation_type),
            preview=normalized_preview[:CONVERSATION_PREVIEW_MAX_CHARS],
            message_count=message_count,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
        )

    @classmethod
    def detail(cls, conversation: Conversation) -> ConversationDetail:
        preview = next(
            (
                message.content
                for message in conversation.messages
                if message.role == "user"
            ),
            "",
        )
        summary = cls._summary(
            conversation,
            preview=preview,
            message_count=len(conversation.messages),
        )
        return ConversationDetail(
            **summary.model_dump(),
            messages=[
                ConversationMessageResponse(
                    id=message.id,
                    role=message.role,
                    content=message.content,
                    created_at=message.created_at,
                )
                for message in conversation.messages
            ],
        )
