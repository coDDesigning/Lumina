"""Timed sittings of a quiz, with the clock on the server.

A countdown in a browser is a suggestion. The candidate owns that machine, so a
timer it keeps is a number they can edit, and an examination whose deadline the
candidate sets is not an examination. Everything that decides whether a sitting
is still open is written and compared here: ``started_at`` and ``expires_at``
are the server's, the client is told the deadline and never sets one.

Answers are saved as they are given rather than posted once at the end. That is
what makes the deadline cost a student the right to keep answering and nothing
else: the work saved before it is never deleted, and a submission that arrives
after it finalises exactly that work. Without drafts, a slow network at minute
fifty-nine would be indistinguishable from a blank paper.

Expiry is derived, never scheduled. A read compares ``expires_at`` against the
clock and reports what it finds; only a write reconciles the stored status
first. So a sitting can be correctly reported as over long before any row says
so, and nothing has to sweep the table to make that true.

Two submissions of one sitting are decided by a guarded update whose
``rowcount`` names the winner -- the idiom job claiming already uses, and the
one that behaves the same on both supported engines, unlike ``SELECT ... FOR
UPDATE`` which SQLite silently ignores. The unique ``attempt_id`` and the
``submitted_state_valid`` constraint are what make a sitting with two attempts
impossible to write down at all; the guard is what makes the race resolve.

Grading is the ordinary grader, and the attempt is an ordinary attempt. This
module owns a clock and a set of drafts. It does not own a second way to mark a
student's work.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from backend.app.models import (
    QUIZ_SESSION_STATUS_ACTIVE,
    QUIZ_SESSION_STATUS_EXPIRED,
    QUIZ_SESSION_STATUS_SUBMITTED,
    Quiz,
    QuizSession,
    QuizSessionAnswer,
)
from schemas.quiz_attempt import (
    QuizAnswerSubmission,
    QuizAttemptRequest,
    QuizAttemptResponse,
)
from services.quiz import QuizService
from services.quiz_attempt import (
    ProviderFactory,
    QuizAttemptService,
    validate_answer_form,
)
from utils.exceptions import BadRequestException, NotFoundException

logger = logging.getLogger(__name__)

SESSION_NOT_FOUND = "Quiz session not found"
NOT_A_TIMED_QUIZ = "This quiz is not sat against a clock."
QUIZ_HAS_NO_QUESTIONS = "This quiz has no questions to answer."
SESSION_EXPIRED = (
    "This exam session has ended. Its saved answers can still be submitted."
)
SESSION_SUBMITTED = "This exam session has already been submitted."
SESSION_EMPTY = "No answers were saved during this exam session."


class QuizSessionError(Exception):
    """Something about a timed sitting is wrong, in a way worth naming."""


class TimedSessionRequiredError(QuizSessionError):
    """A timed quiz was attempted without one, which would bypass the clock."""


class TimedSessionExpiredError(QuizSessionError):
    """The deadline has passed, so nothing further may be written."""


class TimedSessionSubmittedError(QuizSessionError):
    """The sitting is finished and its attempt already exists."""


class TimedSessionEmptyError(QuizSessionError):
    """The sitting ended without a single saved answer to grade."""


@dataclass(frozen=True)
class SessionState:
    """One reading of a sitting, with the clock already applied.

    ``status`` is what the clock says; ``persisted_status`` is what the row
    says. They differ for exactly as long as it takes a write to reconcile
    them, and a reader is entitled to the first.
    """

    session_id: int
    quiz_id: int
    status: str
    persisted_status: str
    started_at: datetime
    expires_at: datetime
    time_limit_seconds: int
    seconds_remaining: int
    elapsed_seconds: int
    attempt_id: int | None
    answered_count: int


def _utc(now: datetime | None) -> datetime:
    """One reading of the clock per call, in UTC.

    Read once and threaded through, so the deadline a response reports and the
    comparison that refused a write can never come from two different instants.
    """
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("A supplied clock reading must carry a timezone")
    return now.astimezone(timezone.utc)


def is_timed(quiz: Quiz) -> bool:
    return bool(quiz.time_limit_seconds and quiz.time_limit_seconds > 0)


class QuizSessionService:
    @staticmethod
    def _effective_status(row: QuizSession, current_time: datetime) -> str:
        if row.status == QUIZ_SESSION_STATUS_ACTIVE and row.expires_at <= current_time:
            return QUIZ_SESSION_STATUS_EXPIRED
        return row.status

    @staticmethod
    def _elapsed_seconds(row: QuizSession, current_time: datetime) -> int:
        """How long the sitting took, capped at the time it was given.

        A submitted sitting reports a fixed number rather than one that grows on
        every re-read, and a late submission never reports more time than the
        paper allowed.
        """
        reference = row.submitted_at or current_time
        raw = int((reference - row.started_at).total_seconds())
        return max(0, min(raw, row.time_limit_seconds))

    @classmethod
    def _state(
        cls, db: Session, row: QuizSession, current_time: datetime
    ) -> SessionState:
        count = db.scalar(
            select(func.count())
            .select_from(QuizSessionAnswer)
            .where(QuizSessionAnswer.session_id == row.id)
        )
        remaining = int((row.expires_at - current_time).total_seconds())
        return SessionState(
            session_id=row.id,
            quiz_id=row.quiz_id,
            status=cls._effective_status(row, current_time),
            persisted_status=row.status,
            started_at=row.started_at,
            expires_at=row.expires_at,
            time_limit_seconds=row.time_limit_seconds,
            seconds_remaining=max(0, remaining),
            elapsed_seconds=cls._elapsed_seconds(row, current_time),
            attempt_id=row.attempt_id,
            answered_count=int(count or 0),
        )

    @staticmethod
    def _timed_quiz(db: Session, course_id: int, quiz_id: int) -> Quiz:
        quiz = QuizService.get_course_quiz(db, course_id, quiz_id)
        if not is_timed(quiz):
            raise BadRequestException(NOT_A_TIMED_QUIZ)
        if not quiz.questions:
            raise BadRequestException(QUIZ_HAS_NO_QUESTIONS)
        return quiz

    @staticmethod
    def _owned_session(
        db: Session, quiz: Quiz, session_id: int, *, user_id: int
    ) -> QuizSession:
        """One sitting, or the same answer a stranger's identifier would get.

        Scoped by quiz and by user in the query rather than checked afterwards,
        so another student's sitting is missing rather than forbidden.
        """
        row = db.scalars(
            select(QuizSession).where(
                QuizSession.id == session_id,
                QuizSession.quiz_id == quiz.id,
                QuizSession.user_id == user_id,
            )
        ).one_or_none()
        if row is None:
            raise NotFoundException(detail=SESSION_NOT_FOUND)
        return row

    @classmethod
    def _reconcile_expiry(
        cls, db: Session, session_id: int, current_time: datetime
    ) -> None:
        """Move a sitting past its deadline to 'expired', in one statement.

        The comparison lives in the WHERE clause rather than in Python so two
        callers racing the deadline cannot both believe they were first.
        """
        db.execute(
            update(QuizSession)
            .where(
                QuizSession.id == session_id,
                QuizSession.status == QUIZ_SESSION_STATUS_ACTIVE,
                QuizSession.expires_at <= current_time,
            )
            .values(
                status=QUIZ_SESSION_STATUS_EXPIRED,
                expired_at=current_time,
                updated_at=current_time,
            )
        )

    @classmethod
    def start_session(
        cls,
        db: Session,
        course_id: int,
        quiz_id: int,
        *,
        user_id: int,
        now: datetime | None = None,
    ) -> SessionState:
        """Open a sitting, or rejoin the one already running.

        A reloaded page must not start a second clock and split the drafts
        between two sittings, so an active sitting is returned rather than
        replaced. Once a sitting is submitted or expired a retake is free to
        start a new one.
        """
        current_time = _utc(now)
        quiz = cls._timed_quiz(db, course_id, quiz_id)

        db.execute(
            update(QuizSession)
            .where(
                QuizSession.quiz_id == quiz.id,
                QuizSession.user_id == user_id,
                QuizSession.status == QUIZ_SESSION_STATUS_ACTIVE,
                QuizSession.expires_at <= current_time,
            )
            .values(
                status=QUIZ_SESSION_STATUS_EXPIRED,
                expired_at=current_time,
                updated_at=current_time,
            )
        )
        db.flush()

        row = QuizSession(
            quiz_id=quiz.id,
            user_id=user_id,
            status=QUIZ_SESSION_STATUS_ACTIVE,
            time_limit_seconds=quiz.time_limit_seconds,
            started_at=current_time,
            expires_at=current_time + timedelta(seconds=quiz.time_limit_seconds),
        )
        try:
            db.add(row)
            db.flush()
            db.commit()
        except IntegrityError:
            # Another request opened the sitting first. Its clock is the real
            # one, so this call joins it rather than starting a second.
            db.rollback()
            existing = db.scalars(
                select(QuizSession).where(
                    QuizSession.quiz_id == quiz.id,
                    QuizSession.user_id == user_id,
                    QuizSession.status == QUIZ_SESSION_STATUS_ACTIVE,
                )
            ).one_or_none()
            if existing is None:
                raise
            return cls._state(db, existing, current_time)

        db.refresh(row)
        return cls._state(db, row, current_time)

    @classmethod
    def get_session(
        cls,
        db: Session,
        course_id: int,
        quiz_id: int,
        session_id: int,
        *,
        user_id: int,
        now: datetime | None = None,
    ) -> SessionState:
        """Read a sitting. Never writes, even when the clock has run out."""
        current_time = _utc(now)
        quiz = cls._timed_quiz(db, course_id, quiz_id)
        row = cls._owned_session(db, quiz, session_id, user_id=user_id)
        return cls._state(db, row, current_time)

    @classmethod
    def save_draft_answer(
        cls,
        db: Session,
        course_id: int,
        quiz_id: int,
        session_id: int,
        answer: QuizAnswerSubmission,
        *,
        user_id: int,
        now: datetime | None = None,
    ) -> SessionState:
        """Record one answer as it stands, replacing whatever it said before.

        Validated against the same rule the attempt endpoint applies, so the
        server can never accept a draft it will later refuse to grade.
        """
        current_time = _utc(now)
        quiz = cls._timed_quiz(db, course_id, quiz_id)
        row = cls._owned_session(db, quiz, session_id, user_id=user_id)

        if row.status == QUIZ_SESSION_STATUS_SUBMITTED:
            raise TimedSessionSubmittedError(SESSION_SUBMITTED)

        cls._reconcile_expiry(db, row.id, current_time)
        db.commit()
        db.refresh(row)

        if cls._effective_status(row, current_time) != QUIZ_SESSION_STATUS_ACTIVE:
            # The drafts already saved are untouched: the deadline stops new
            # writing, it does not discard work that already landed.
            raise TimedSessionExpiredError(SESSION_EXPIRED)

        question = next(
            (item for item in quiz.questions if item.id == answer.question_id), None
        )
        if question is None:
            raise BadRequestException(
                "One of the submitted answers does not belong to this quiz."
            )
        validate_answer_form(answer, question)

        existing = db.scalars(
            select(QuizSessionAnswer).where(
                QuizSessionAnswer.session_id == row.id,
                QuizSessionAnswer.quiz_question_id == question.id,
            )
        ).one_or_none()

        try:
            if existing is None:
                db.add(
                    QuizSessionAnswer(
                        session_id=row.id,
                        quiz_question_id=question.id,
                        selected_option_index=answer.selected_option_index,
                        text_response=answer.text_response,
                        time_spent_seconds=answer.time_spent_seconds,
                    )
                )
            else:
                existing.selected_option_index = answer.selected_option_index
                existing.text_response = answer.text_response
                existing.time_spent_seconds = answer.time_spent_seconds
                existing.updated_at = current_time
            db.commit()
        except Exception:
            db.rollback()
            raise

        db.refresh(row)
        return cls._state(db, row, current_time)

    @classmethod
    def _drafts_as_request(cls, db: Session, row: QuizSession) -> QuizAttemptRequest:
        drafts = db.scalars(
            select(QuizSessionAnswer)
            .where(QuizSessionAnswer.session_id == row.id)
            .order_by(QuizSessionAnswer.quiz_question_id)
        ).all()
        if not drafts:
            raise TimedSessionEmptyError(SESSION_EMPTY)
        return QuizAttemptRequest(
            answers=[
                QuizAnswerSubmission(
                    question_id=draft.quiz_question_id,
                    selected_option_index=draft.selected_option_index,
                    text_response=draft.text_response,
                    time_spent_seconds=draft.time_spent_seconds,
                )
                for draft in drafts
            ]
        )

    @classmethod
    def submit_session(
        cls,
        db: Session,
        course_id: int,
        quiz_id: int,
        session_id: int,
        *,
        user_id: int,
        provider_factory: ProviderFactory | None = None,
        now: datetime | None = None,
    ) -> QuizAttemptResponse:
        """Finalise a sitting into an ordinary attempt, exactly once.

        The answers finalised are the drafts saved before the deadline. Answers
        supplied with the submission itself are not accepted here at all: a
        request that arrives after time is up must not be able to change what
        was written, and one that arrives before it had every opportunity to
        save.

        The contested write is the last statement before the commit, so no row
        lock is held across the grading call.
        """
        try:
            return cls._submit_once(
                db,
                course_id,
                quiz_id,
                session_id,
                user_id=user_id,
                provider_factory=provider_factory,
                now=now,
            )
        except OperationalError:
            # SQLite refuses a write whose snapshot another writer has already
            # superseded, immediately and without waiting. One retry is enough:
            # the re-read sees the winner and returns its attempt.
            db.rollback()
            logger.info("Retrying a timed submission that lost a write race")
            return cls._submit_once(
                db,
                course_id,
                quiz_id,
                session_id,
                user_id=user_id,
                provider_factory=provider_factory,
                now=now,
            )

    @classmethod
    def _submit_once(
        cls,
        db: Session,
        course_id: int,
        quiz_id: int,
        session_id: int,
        *,
        user_id: int,
        provider_factory: ProviderFactory | None,
        now: datetime | None,
    ) -> QuizAttemptResponse:
        current_time = _utc(now)
        quiz = cls._timed_quiz(db, course_id, quiz_id)
        row = cls._owned_session(db, quiz, session_id, user_id=user_id)

        if row.status == QUIZ_SESSION_STATUS_SUBMITTED and row.attempt_id is not None:
            # Already finished. Returning the same attempt costs no provider
            # call, writes no rows, and moves progress not at all.
            return QuizAttemptService.get_attempt_detail(
                db, course_id, quiz_id, row.attempt_id, user_id=user_id
            )

        expired = (
            cls._effective_status(row, current_time) == QUIZ_SESSION_STATUS_EXPIRED
        )
        request = cls._drafts_as_request(db, row)
        elapsed = (
            row.time_limit_seconds
            if expired
            else cls._elapsed_seconds(row, current_time)
        )

        attempt = QuizAttemptService.record_attempt(
            db,
            course_id,
            quiz_id,
            request,
            user_id=user_id,
            provider_factory=provider_factory,
            time_spent_seconds_override=elapsed,
            commit=False,
        )

        claimed = db.execute(
            update(QuizSession)
            .where(
                QuizSession.id == row.id,
                QuizSession.status != QUIZ_SESSION_STATUS_SUBMITTED,
            )
            .values(
                status=QUIZ_SESSION_STATUS_SUBMITTED,
                submitted_at=current_time,
                attempt_id=attempt.attempt_id,
                expired_at=row.expired_at or (current_time if expired else None),
                updated_at=current_time,
            )
        ).rowcount

        if claimed != 1:
            # Another submission of this sitting won. Everything this one
            # graded is discarded rather than recorded a second time.
            db.rollback()
            winner = db.scalars(
                select(QuizSession).where(QuizSession.id == row.id)
            ).one()
            if winner.attempt_id is None:
                raise TimedSessionSubmittedError(SESSION_SUBMITTED)
            return QuizAttemptService.get_attempt_detail(
                db, course_id, quiz_id, winner.attempt_id, user_id=user_id
            )

        db.commit()
        # Re-read so the response reports the sitting's own facts -- whether the
        # deadline had passed above all -- which only exist once it is linked.
        return QuizAttemptService.get_attempt_detail(
            db, course_id, quiz_id, attempt.attempt_id, user_id=user_id
        )
