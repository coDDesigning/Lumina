"""What a student has already paid for in Exam Mode, and when they pay.

Exam Mode charges per topic, once. Unlocking a topic buys everything the
feature makes for it — the guide, the summary, the practice questions, the
topic exam, the similar questions — and the charge lands the first time the
student asks for any of them.

Charging lazily rather than at plan time is the point. A student who plans
twelve topics and studies four should pay for four; charging up front would
bill for generations nobody asked for and spend provider budget on them.

The unlock outlives the plan that first named the topic, so regenerating a
plan over the same topics costs nothing. That is why the row is keyed by
(course, student, topic) and holds no plan identifier: a topic is the same
topic whichever plan surfaced it, and ``canonical_topic_key`` is what makes
that claim checkable.
"""

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.models import ExamTopicUnlock
from services.credits import GENERATION_CREDIT_COSTS, CreditService
from utils.ai_errors import InsufficientCreditsError

logger = logging.getLogger(__name__)

UNLOCK_SOURCE_TYPE = "exam_topic_unlock"

INSUFFICIENT_CREDITS_MESSAGE = "Insufficient credits."


@dataclass(frozen=True)
class TopicUnlock:
    """One topic's access, and whether this call is what bought it."""

    topic_key: str
    charged: bool
    amount: float


class ExamEntitlementService:
    @staticmethod
    def unlocked_topic_keys(db: Session, course_id: int, user_id: int) -> set[str]:
        return set(
            db.scalars(
                select(ExamTopicUnlock.topic_key).where(
                    ExamTopicUnlock.course_id == course_id,
                    ExamTopicUnlock.user_id == user_id,
                )
            ).all()
        )

    @staticmethod
    def is_unlocked(db: Session, course_id: int, user_id: int, topic_key: str) -> bool:
        return (
            db.scalar(
                select(ExamTopicUnlock.id).where(
                    ExamTopicUnlock.course_id == course_id,
                    ExamTopicUnlock.user_id == user_id,
                    ExamTopicUnlock.topic_key == topic_key,
                )
            )
            is not None
        )

    @classmethod
    def ensure_unlocked(
        cls, db: Session, course_id: int, user_id: int, topic_key: str
    ) -> TopicUnlock:
        """Buy this topic if the student has not already bought it.

        Returns without charging when the row exists, which is what makes the
        second artifact for a topic free and a regenerated plan free.

        Commits the unlock on its own. The alternative — carrying the charge
        inside the generation's transaction — would mean a student paid twice
        for a topic whose first generation failed after the charge and rolled
        it back, because the retry would find no row. Paying once and keeping
        the entitlement is the honest failure mode: the price bought access to
        the topic, and access is what survives.

        A concurrent request that wins the unique key first is treated as the
        same purchase rather than a second one; its charge is reversed so two
        requests can never bill one topic twice.
        """
        existing = db.scalar(
            select(ExamTopicUnlock).where(
                ExamTopicUnlock.course_id == course_id,
                ExamTopicUnlock.user_id == user_id,
                ExamTopicUnlock.topic_key == topic_key,
            )
        )
        if existing is not None:
            return TopicUnlock(topic_key=topic_key, charged=False, amount=0.0)

        price = GENERATION_CREDIT_COSTS[UNLOCK_SOURCE_TYPE]
        receipt = CreditService.charge(
            db, user_id, price, source_type=UNLOCK_SOURCE_TYPE
        )
        if receipt is None:
            raise InsufficientCreditsError(INSUFFICIENT_CREDITS_MESSAGE)

        db.add(
            ExamTopicUnlock(
                course_id=course_id,
                user_id=user_id,
                topic_key=topic_key,
                credit_transaction_id=receipt.transaction_id,
                amount=price,
            )
        )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            CreditService.refund(db, receipt)
            db.commit()
            logger.info(
                "An exam topic was unlocked concurrently; the charge was undone"
            )
            return TopicUnlock(topic_key=topic_key, charged=False, amount=0.0)

        return TopicUnlock(topic_key=topic_key, charged=True, amount=price)
