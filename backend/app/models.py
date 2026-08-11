from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    false,
    func,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from .base import Base


JOB_TYPE_EXTRACT_DOCUMENT = "extract_document"
JOB_STATUS_QUEUED = "queued"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_SUCCEEDED = "succeeded"
JOB_STATUS_FAILED = "failed"


class UTCDateTime(TypeDecorator[datetime]):
    """Persist UTC job timestamps consistently across supported databases."""

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect):
        return dialect.type_descriptor(DateTime(timezone=dialect.name != "sqlite"))

    def process_bind_param(self, value: datetime | None, dialect: Dialect):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("UTCDateTime values must be timezone-aware")
        value = value.astimezone(timezone.utc)
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value: datetime | None, _dialect: Dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)

    users: Mapped[list["User"]] = relationship(back_populates="role")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("email = lower(email)", name="email_lowercase"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"))
    is_initial_admin: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, unique=True
    )
    credits: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_banned: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    preferred_model: Mapped[str] = mapped_column(
        String(100), default="gpt-4o-mini", server_default="gpt-4o-mini"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    role: Mapped["Role"] = relationship(back_populates="users")

    courses: Mapped[list["Course"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan", passive_deletes=True
    )
    uploaded_documents: Mapped[list["UploadedDocument"]] = relationship(
        back_populates="uploader",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    # Every quiz attempt this user has made.
    quiz_attempts: Mapped[list["QuizAttempt"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )

    # This user's per-course progress rows.
    progress_rows: Mapped[list["Progress"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )

    # What we know about this user's knowledge, item by item.
    knowledge_items: Mapped[list["ProfileKnowledge"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )


class Course(Base):
    __tablename__ = "courses"
    __table_args__ = (CheckConstraint("price >= 0", name="price_nonnegative"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    instructor: Mapped[str] = mapped_column(String(200))
    price: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    owner: Mapped["User"] = relationship(back_populates="courses")
    documents: Mapped[list["UploadedDocument"]] = relationship(
        back_populates="course", cascade="all, delete-orphan", passive_deletes=True
    )

    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="course",
        cascade="all, delete-orphan",
        passive_deletes=True,
        overlaps="document,chunks",
    )

    # AI-generated artifacts (summaries and similar) for this course.
    generated_outputs: Mapped[list["GeneratedOutput"]] = relationship(
        back_populates="course", cascade="all, delete-orphan", passive_deletes=True
    )

    # Quizzes belonging to this course.
    quizzes: Mapped[list["Quiz"]] = relationship(
        back_populates="course", cascade="all, delete-orphan", passive_deletes=True
    )

    # Per-user progress rows for this course.
    progress_rows: Mapped[list["Progress"]] = relationship(
        back_populates="course", cascade="all, delete-orphan", passive_deletes=True
    )


class UploadedDocument(Base):
    __tablename__ = "uploaded_documents"
    __table_args__ = (
        UniqueConstraint(
            "course_id",
            "file_hash",
            name="uq_uploaded_documents_course_id_file_hash",
        ),
        UniqueConstraint(
            "id",
            "course_id",
            name="uq_uploaded_documents_id_course_id",
        ),
        CheckConstraint("length(file_hash) = 64", name="file_hash_length"),
        CheckConstraint("file_size >= 0", name="file_size_nonnegative"),
        CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed')",
            name="status_valid",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    original_file_name: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(50))
    mime_type: Mapped[str] = mapped_column(String(255))
    file_size: Mapped[int] = mapped_column(BigInteger)
    file_hash: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    storage_provider: Mapped[str] = mapped_column(String(50))
    storage_key: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending"
    )
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    uploader: Mapped["User"] = relationship(back_populates="uploaded_documents")
    course: Mapped["Course"] = relationship(back_populates="documents")
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        overlaps="course,chunks",
    )
    processing_jobs: Mapped[list["ProcessingJob"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_chunk_doc_index"),
        CheckConstraint(
            "page_number IS NULL OR page_number >= 1", name="page_number_positive"
        ),
        ForeignKeyConstraint(
            ["document_id", "course_id"],
            ["uploaded_documents.id", "uploaded_documents.course_id"],
            name="fk_document_chunks_document_course_uploaded_documents",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    document_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        index=True,
    )

    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )

    chunk_index: Mapped[int] = mapped_column(Integer)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)

    text: Mapped[str] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    document: Mapped["UploadedDocument"] = relationship(
        back_populates="chunks", overlaps="course,chunks"
    )
    course: Mapped["Course"] = relationship(
        back_populates="chunks", overlaps="document,chunks"
    )


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "course_id"],
            ["uploaded_documents.id", "uploaded_documents.course_id"],
            name="fk_processing_jobs_document_course_uploaded_documents",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "document_id", "job_type", name="uq_processing_jobs_document_type"
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="status_valid",
        ),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        CheckConstraint("max_attempts > 0", name="max_attempts_positive"),
        CheckConstraint(
            "attempt_count <= max_attempts", name="attempt_count_within_limit"
        ),
        CheckConstraint(
            "status <> 'queued' OR attempt_count < max_attempts",
            name="queued_attempts_available",
        ),
        CheckConstraint(
            "(status = 'running' AND attempt_count > 0 "
            "AND lease_owner IS NOT NULL AND claim_token IS NOT NULL "
            "AND claimed_at IS NOT NULL AND heartbeat_at IS NOT NULL "
            "AND lease_expires_at IS NOT NULL "
            "AND heartbeat_at >= claimed_at "
            "AND lease_expires_at > heartbeat_at AND finished_at IS NULL) OR "
            "(status <> 'running' AND lease_owner IS NULL "
            "AND claim_token IS NULL AND claimed_at IS NULL "
            "AND heartbeat_at IS NULL AND lease_expires_at IS NULL)",
            name="lease_state_valid",
        ),
        CheckConstraint(
            "(status IN ('succeeded', 'failed') AND finished_at IS NOT NULL) OR "
            "(status IN ('queued', 'running') AND finished_at IS NULL)",
            name="finished_state_valid",
        ),
        Index("ix_processing_jobs_claimable", "status", "available_at", "id"),
        Index("ix_processing_jobs_recoverable", "status", "lease_expires_at", "id"),
        Index("ix_processing_jobs_course_created", "course_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    course_id: Mapped[int] = mapped_column(Integer)
    job_type: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(
        String(20), default=JOB_STATUS_QUEUED, server_default=JOB_STATUS_QUEUED
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer)
    available_at: Mapped[datetime] = mapped_column(UTCDateTime())
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    lease_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    claim_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now()
    )

    document: Mapped["UploadedDocument"] = relationship(
        back_populates="processing_jobs"
    )


class GeneratedOutput(Base):
    """An AI-generated artifact for a course, such as a summary.

    We store WHAT was generated (content), WHAT KIND it is (output_type),
    and WHICH COURSE it belongs to. It lives under a course, so when the
    course is deleted, its generated outputs are deleted with it.
    """

    __tablename__ = "generated_outputs"

    # The auto-numbered identity of this row. Every table gets one.
    id: Mapped[int] = mapped_column(primary_key=True)

    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )

    # What kind of output this is, stored as a short text label,
    # for example: "summary". Later kinds can be added without
    # changing the table structure.
    output_type: Mapped[str] = mapped_column(String(30))

    content: Mapped[str] = mapped_column(Text)  # Unlimited length

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Navigation attribute (NOT a column): lets Python code write
    # output.course to reach the Course object. The partner attribute
    # on Course must be named exactly "generated_outputs".
    course: Mapped["Course"] = relationship(back_populates="generated_outputs")


class Quiz(Base):
    """A quiz that belongs to one course."""

    __tablename__ = "quizzes"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Owner course. Same cascade logic.

    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )

    title: Mapped[str] = mapped_column(String(200))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Navigation up to the course
    course: Mapped["Course"] = relationship(back_populates="quizzes")

    questions: Mapped[list["QuizQuestion"]] = relationship(
        back_populates="quiz", cascade="all, delete-orphan", passive_deletes=True
    )

    attempts: Mapped[list["QuizAttempt"]] = relationship(
        back_populates="quiz", cascade="all, delete-orphan", passive_deletes=True
    )


class QuizQuestion(Base):
    """One question inside a quiz, with its options and correct answer."""

    __tablename__ = "quiz_questions"

    # A quiz cannot contain two questions claiming the same position,
    # so the PAIR (quiz_id, question_index) must be unique. This is a
    # composite unique constraint: each column alone may repeat, the
    # combination may not.

    __table_args__ = (
        UniqueConstraint("quiz_id", "question_index", name="uq_question_quiz_index"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    quiz_id: Mapped[int] = mapped_column(
        ForeignKey("quizzes.id", ondelete="CASCADE"), index=True
    )

    # The position of this question inside its quiz: 0,1,2,3...
    question_index: Mapped[int] = mapped_column(Integer)

    # The question text itself
    question_text: Mapped[str] = mapped_column(Text)

    # The answer options stored as one JSON value, for example:
    # ["Paris", "London", "Berlin", "Madrid"]. We chose JSON instead
    # of a separate options table because options are only ever read
    # and written together as one bundle

    options: Mapped[list] = mapped_column(JSON)

    correct_option_index: Mapped[int] = mapped_column(Integer)

    quiz: Mapped["Quiz"] = relationship(back_populates="questions")


class QuizAttempt(Base):
    """The event of one user taking one quiz once.

    This table points at TWO parents, because an attempt only makes
    sense as "this person, this quiz". A user may attempt the same
    quiz many times, so there is deliberately NO unique constraint
    on the (user, quiz) pair here
    """

    __tablename__ = "quiz_attempts"
    __table_args__ = (
        CheckConstraint("score >= 0 AND score <= 1", name="score_fraction"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # Parent one: who attempted. User deleted -> attempts deleted.
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    # Parent two: which quiz. Quiz deleted -> attempts deleted.
    quiz_id: Mapped[int] = mapped_column(
        ForeignKey("quizzes.id", ondelete="CASCADE"), index=True
    )

    # The result as a fraction between 0.0 and 1.0. Float is the
    # column type for decimal numbers.
    score: Mapped[float] = mapped_column(Float)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="quiz_attempts")
    quiz: Mapped["Quiz"] = relationship(back_populates="attempts")


class Progress(Base):
    """How far one user has come in one course - exactly one row per pair.

    This is the composite unique constraint from our early design
    discussion, finally in its intended home: the same user and the
    same course may appear in many rows separately, but the PAIR may
    exist only once. The database physically rejects a second row for
    the same pair, so application code should update the existing row
    instead of inserting a new one.
    """

    __tablename__ = "progress"
    __table_args__ = (
        UniqueConstraint("user_id", "course_id", name="uq_progress_user_course"),
        CheckConstraint(
            "completion >= 0 AND completion <= 1",
            name="completion_fraction",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )

    # Completion as a fraction from 0.0 to 1.0.
    completion: Mapped[float] = mapped_column(Float, default=0.0)

    # SQLAlchemy includes the onupdate expression in ORM-generated updates.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="progress_rows")
    course: Mapped["Course"] = relationship(back_populates="progress_rows")


class ProfileKnowledge(Base):
    """One piece of what we know about a user's knowledge profile.

    Stored as simple topic + level rows so the AI layer can later ask
    "what does this user already know?"
    """

    __tablename__ = "profile_knowledge"

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    # What topic this row describes, for example "linear algebra".
    topic: Mapped[str] = mapped_column(String(200))

    # A free-text description of the user's level or state on the
    # topic. Text, because the AI layer may write paragraphs here.
    detail: Mapped[str] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="knowledge_items")
