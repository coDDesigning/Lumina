import math
import struct
from datetime import date, datetime, timezone
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
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
    inspect,
    text,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import LargeBinary, TypeDecorator

from .base import Base


QUESTION_TYPE_MULTIPLE_CHOICE = "multiple_choice"

CONVERSATION_TYPES = ("course_qa", "ai_tutor")
_CONVERSATION_TYPES_SQL = ", ".join(f"'{kind}'" for kind in CONVERSATION_TYPES)

EDUCATION_LEVELS = (
    "high_school",
    "undergraduate",
    "graduate",
    "professional_other",
    "unspecified",
)
_EDUCATION_LEVELS_SQL = ", ".join(f"'{level}'" for level in EDUCATION_LEVELS)

DOCUMENT_MATERIAL_KINDS = (
    "lecture_notes",
    "slides",
    "textbook",
    "syllabus",
    "assignment",
    "past_exam",
    "article",
    "notes",
    "other",
    "unspecified",
)
_DOCUMENT_MATERIAL_KINDS_SQL = ", ".join(
    f"'{kind}'" for kind in DOCUMENT_MATERIAL_KINDS
)

OUTPUT_TYPE_EXAM_TOPIC_ANALYSIS = "exam_topic_analysis"
OUTPUT_TYPE_EXAM_PLAN = "exam_plan"
OUTPUT_TYPE_EXAM_TOPIC_GUIDE = "exam_topic_guide"
OUTPUT_TYPE_EXAM_TOPIC_SUMMARY = "exam_topic_summary"
OUTPUT_TYPE_EXAM_TOPIC_PRACTICE = "exam_topic_practice"
OUTPUT_TYPE_EXAM_TOPIC_EXAM = "exam_topic_exam"
OUTPUT_TYPE_EXAM_SIMILAR_QUESTIONS = "exam_similar_questions"
OUTPUT_TYPE_EXAM_MOCK_EXAM = "exam_mock_exam"
OUTPUT_TYPE_EXAM_REVIEW_SHEET = "exam_review_sheet"

EXAM_QUESTION_TYPES = (
    "multiple_choice",
    "true_false",
    "short_answer",
    "structured",
    "essay",
    "problem",
    "proof",
    "unspecified",
)
_EXAM_QUESTION_TYPES_SQL = ", ".join(f"'{kind}'" for kind in EXAM_QUESTION_TYPES)

EXAM_QUESTION_DIFFICULTIES = ("easy", "medium", "hard")

QUIZ_PURPOSE_PRACTICE = "practice"
QUIZ_PURPOSE_EXAM_TOPIC_PRACTICE = "exam_topic_practice"
QUIZ_PURPOSE_EXAM_TOPIC_EXAM = "exam_topic_exam"
QUIZ_PURPOSE_EXAM_SIMILAR_QUESTIONS = "exam_similar_questions"
QUIZ_PURPOSE_EXAM_MOCK_EXAM = "exam_mock_exam"
QUIZ_PURPOSES = (
    QUIZ_PURPOSE_PRACTICE,
    QUIZ_PURPOSE_EXAM_TOPIC_PRACTICE,
    QUIZ_PURPOSE_EXAM_TOPIC_EXAM,
    QUIZ_PURPOSE_EXAM_SIMILAR_QUESTIONS,
    QUIZ_PURPOSE_EXAM_MOCK_EXAM,
)
EXAM_QUIZ_PURPOSES = (
    QUIZ_PURPOSE_EXAM_TOPIC_PRACTICE,
    QUIZ_PURPOSE_EXAM_TOPIC_EXAM,
    QUIZ_PURPOSE_EXAM_SIMILAR_QUESTIONS,
    QUIZ_PURPOSE_EXAM_MOCK_EXAM,
)
ANSWER_HIDDEN_QUIZ_PURPOSES = (
    QUIZ_PURPOSE_EXAM_TOPIC_EXAM,
    QUIZ_PURPOSE_EXAM_SIMILAR_QUESTIONS,
    QUIZ_PURPOSE_EXAM_MOCK_EXAM,
)

QUIZ_SESSION_STATUS_ACTIVE = "active"
QUIZ_SESSION_STATUS_SUBMITTED = "submitted"
QUIZ_SESSION_STATUS_EXPIRED = "expired"
QUIZ_SESSION_STATUSES = (
    QUIZ_SESSION_STATUS_ACTIVE,
    QUIZ_SESSION_STATUS_SUBMITTED,
    QUIZ_SESSION_STATUS_EXPIRED,
)
_QUIZ_SESSION_STATUSES_SQL = ", ".join(
    f"'{status}'" for status in QUIZ_SESSION_STATUSES
)

MAX_QUIZ_TIME_LIMIT_SECONDS = 86_400
MAX_GENERATION_REQUEST_ID_CHARS = 64

EXAM_EXTRACTION_NOT_APPLICABLE = "not_applicable"
EXAM_EXTRACTION_PENDING = "pending"
EXAM_EXTRACTION_SUCCEEDED = "succeeded"
EXAM_EXTRACTION_FAILED = "failed"
EXAM_EXTRACTION_NOT_CONFIGURED = "not_configured"
EXAM_EXTRACTION_SKIPPED = "skipped"
EXAM_EXTRACTION_STATUSES = (
    EXAM_EXTRACTION_NOT_APPLICABLE,
    EXAM_EXTRACTION_PENDING,
    EXAM_EXTRACTION_SUCCEEDED,
    EXAM_EXTRACTION_FAILED,
    EXAM_EXTRACTION_NOT_CONFIGURED,
    EXAM_EXTRACTION_SKIPPED,
)
_EXAM_QUESTION_DIFFICULTIES_SQL = ", ".join(
    f"'{level}'" for level in EXAM_QUESTION_DIFFICULTIES
)

JOB_TYPE_EXTRACT_DOCUMENT = "extract_document"
JOB_STATUS_QUEUED = "queued"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_SUCCEEDED = "succeeded"
JOB_STATUS_FAILED = "failed"
DOCUMENT_PROCESSING_STAGES = (
    "validating",
    "extracting_text",
    "running_ocr",
    "understanding_images",
    "cleaning_text",
    "chunking",
    "generating_embeddings",
)
_DOCUMENT_PROCESSING_STAGES_SQL = ", ".join(
    f"'{stage}'" for stage in DOCUMENT_PROCESSING_STAGES
)
_ASCII_WHITESPACE = " \t\n\r\v\f"

EMBEDDING_DIMENSIONS = 768


class UTCDateTime(TypeDecorator[datetime]):
    """Persist UTC timestamps consistently across supported databases."""

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


class EmbeddingVector(TypeDecorator[list[float]]):
    """Persist fixed-width float vectors on both supported databases.

    PostgreSQL stores a native pgvector column so similarity search and the
    HNSW index work. SQLite has no vector type, so the same logical value is
    packed as little-endian float32 and unpacked on read. Both dialects hand
    the application an ordinary list of floats.
    """

    impl = LargeBinary
    cache_ok = True

    _STRUCT = struct.Struct(f"<{EMBEDDING_DIMENSIONS}f")

    def load_dialect_impl(self, dialect: Dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Vector(EMBEDDING_DIMENSIONS))
        return dialect.type_descriptor(LargeBinary(self._STRUCT.size))

    def process_bind_param(self, value, dialect: Dialect):
        if value is None:
            return None
        values = [float(item) for item in value]
        if len(values) != EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"Embedding vectors must contain {EMBEDDING_DIMENSIONS} values"
            )
        if not all(math.isfinite(item) for item in values):
            raise ValueError("Embedding vectors must contain finite values")
        if dialect.name == "postgresql":
            return values
        return self._STRUCT.pack(*values)

    def process_result_value(self, value, dialect: Dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return [float(item) for item in value]
        return list(self._STRUCT.unpack(value))


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)

    users: Mapped[list["User"]] = relationship(back_populates="role")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        CheckConstraint(
            f"education_level IN ({_EDUCATION_LEVELS_SQL})",
            name="education_level_valid",
        ),
    )

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
    # When the address was proven reachable, not merely whether it was. The
    # instant is what makes the fact auditable, and null is the honest value
    # for a deployment that never asks -- it claims nothing either way.
    email_verified_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    tokens_valid_after: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    education_level: Mapped[str] = mapped_column(
        String(20), default="unspecified", server_default="unspecified"
    )
    preferred_model: Mapped[str] = mapped_column(
        String(100),
        default="gemini:gemini-3.6-flash",
        server_default="gemini:gemini-3.6-flash",
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now()
    )

    role: Mapped["Role"] = relationship(back_populates="users")

    courses: Mapped[list["Course"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan", passive_deletes=True
    )

    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    uploaded_documents: Mapped[list["UploadedDocument"]] = relationship(
        back_populates="uploader",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    # Every quiz attempt this user has made.
    quizzes: Mapped[list["Quiz"]] = relationship(back_populates="user")

    quiz_sessions: Mapped[list["QuizSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )

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

    # Personal background documents uploaded by this user.
    profile_documents: Mapped[list["ProfileDocument"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )

    # Privacy-safe structured AI usage logs recorded for this user.
    ai_usage_logs: Mapped[list["AiUsageLog"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )

    # Every balance change ever applied to this account.
    credit_transactions: Mapped[list["CreditTransaction"]] = relationship(
        back_populates="user",
        foreign_keys="CreditTransaction.user_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="CreditTransaction.id",
    )

    email_verification_tokens: Mapped[list["EmailVerificationToken"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    password_reset_tokens: Mapped[list["PasswordResetToken"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    revoked_tokens: Mapped[list["RevokedToken"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class EmailVerificationToken(Base):
    """One issued email-verification link, stored as a hash of the credential.

    The emailed token is a bearer credential, so only its SHA-256 digest is
    kept: a database read cannot verify anybody, and a leaked backup does not
    hand over live links.

    Expiry and single use are both properties of this row rather than of the
    handler. ``expires_at`` is compared in the statement that reads the row, the
    way every other deadline in this schema is, so nothing has to be scheduled;
    ``consumed_at`` is set by a guarded update whose ``WHERE`` requires it to
    still be null, so two clicks on one link cannot both redeem it. Issuing a
    replacement consumes the outstanding ones, which is what keeps the number of
    live links per account at one. See docs/authentication.md.
    """

    __tablename__ = "email_verification_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="email_verification_tokens")


class PasswordResetToken(Base):
    """One issued password reset link, stored as a hash of the credential.
    
    Like EmailVerificationToken, the emailed token is a bearer credential, so
    only its SHA-256 digest is kept.
    """

    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="password_reset_tokens")


class RevokedToken(Base):
    """A revoked JWT jti stored until its natural expiration."""

    __tablename__ = "revoked_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="revoked_tokens")


class Course(Base):
    __tablename__ = "courses"
    __table_args__ = (
        CheckConstraint(
            f"education_level IN ({_EDUCATION_LEVELS_SQL})",
            name="education_level_valid",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    subject_area: Mapped[str | None] = mapped_column(String(100), nullable=True)
    education_level: Mapped[str] = mapped_column(
        String(20), default="unspecified", server_default="unspecified"
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    semester: Mapped[str | None] = mapped_column(String(100), nullable=True)
    exam_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    syllabus: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    is_archived: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    owner: Mapped["User"] = relationship(back_populates="courses")

    @property
    def owner_name(self) -> str | None:
        return self.owner.name if self.owner is not None else None

    @property
    def topics(self) -> list[str]:
        return [topic.name for topic in self.topic_rows]

    @property
    def owner_email(self) -> str | None:
        return self.owner.email if self.owner is not None else None

    documents: Mapped[list["UploadedDocument"]] = relationship(
        back_populates="course", cascade="all, delete-orphan", passive_deletes=True
    )

    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="course",
        cascade="all, delete-orphan",
        passive_deletes=True,
        overlaps="document,chunks",
    )
    document_pages: Mapped[list["DocumentPage"]] = relationship(
        back_populates="course",
        cascade="all, delete-orphan",
        passive_deletes=True,
        overlaps="document,document_pages",
    )

    # AI-generated artifacts (summaries and similar) for this course.
    generated_outputs: Mapped[list["GeneratedOutput"]] = relationship(
        back_populates="course", cascade="all, delete-orphan", passive_deletes=True
    )

    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="course",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    # Quizzes belonging to this course.
    quizzes: Mapped[list["Quiz"]] = relationship(
        back_populates="course", cascade="all, delete-orphan", passive_deletes=True
    )

    # Per-user progress rows for this course.
    progress_rows: Mapped[list["Progress"]] = relationship(
        back_populates="course", cascade="all, delete-orphan", passive_deletes=True
    )

    # Privacy-safe AI generation telemetry scoped to this course.
    ai_usage_logs: Mapped[list["AiUsageLog"]] = relationship(
        back_populates="course", cascade="all, delete-orphan", passive_deletes=True
    )

    settings: Mapped["CourseSettings | None"] = relationship(
        back_populates="course",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    topic_rows: Mapped[list["CourseTopic"]] = relationship(
        back_populates="course",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="CourseTopic.position",
    )


class CourseTopic(Base):
    __tablename__ = "course_topics"
    __table_args__ = (
        UniqueConstraint("course_id", "name", name="uq_course_topics_course_id_name"),
        CheckConstraint("position >= 0", name="position_nonnegative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(100))

    course: Mapped["Course"] = relationship(back_populates="topic_rows")


class CourseSettings(Base):
    __tablename__ = "course_settings"
    __table_args__ = (
        UniqueConstraint("course_id", name="uq_course_settings_course_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    study_mode: Mapped[str] = mapped_column(
        String(50), default="Exam", server_default="Exam"
    )
    difficulty: Mapped[str] = mapped_column(
        String(50), default="Adaptive", server_default="Adaptive"
    )
    question_count: Mapped[int] = mapped_column(
        Integer, default=10, server_default="10"
    )
    summary_length: Mapped[str] = mapped_column(
        String(50), default="Medium", server_default="Medium"
    )
    detail_level: Mapped[str] = mapped_column(
        String(50), default="Balanced", server_default="Balanced"
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    course: Mapped["Course"] = relationship(back_populates="settings")


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
            "status IN ('uploaded', 'processing', 'ready', 'failed', 'deleting')",
            name="status_valid",
        ),
        CheckConstraint(
            f"material_kind IN ({_DOCUMENT_MATERIAL_KINDS_SQL})",
            name="material_kind_valid",
        ),
        Index(
            "uq_uploaded_documents_storage_provider_storage_key",
            "storage_provider",
            "storage_key",
            unique=True,
        ),
        Index(
            "ix_uploaded_documents_course_status_created",
            "course_id",
            "status",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    original_file_name: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(50))
    mime_type: Mapped[str] = mapped_column(String(255))
    material_kind: Mapped[str] = mapped_column(
        String(20), default="unspecified", server_default="unspecified"
    )
    file_size: Mapped[int] = mapped_column(BigInteger)
    file_hash: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    storage_provider: Mapped[str] = mapped_column(String(50))
    storage_key: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(
        String(20), default="uploaded", server_default="uploaded"
    )
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    exam_extraction_status: Mapped[str] = mapped_column(
        String(20),
        default=EXAM_EXTRACTION_NOT_APPLICABLE,
        server_default=EXAM_EXTRACTION_NOT_APPLICABLE,
    )
    exam_extraction_error_code: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now()
    )

    uploader: Mapped["User"] = relationship(back_populates="uploaded_documents")
    course: Mapped["Course"] = relationship(back_populates="documents")
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="DocumentChunk.chunk_index",
        overlaps="course,chunks",
    )
    pages: Mapped[list["DocumentPage"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="DocumentPage.content_index",
        overlaps="course,document_pages",
    )
    processing_jobs: Mapped[list["ProcessingJob"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    past_exam_questions: Mapped[list["PastExamQuestion"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="PastExamQuestion.position",
        overlaps="course,past_exam_questions",
    )

    @property
    def visual_analysis_status(self) -> str:
        pages = None
        try:
            insp = inspect(self)
            if insp is not None and "pages" not in insp.unloaded:
                pages = self.pages
        except Exception:
            pages = getattr(self, "__dict__", {}).get("pages")

        if not pages:
            if self.file_type != "pdf":
                return "not_applicable"
            if self.status in ("uploaded", "processing"):
                return "pending"
            return "not_applicable"

        visual_pages = [p for p in pages if getattr(p, "has_visual_content", False)]
        if not visual_pages:
            return "not_applicable"

        statuses = {
            getattr(p, "visual_analysis_status", "not_applicable") for p in visual_pages
        }
        if "pending" in statuses:
            return "pending"
        if statuses == {"completed"}:
            return "completed"
        if statuses == {"not_configured"}:
            return "not_configured"
        if statuses == {"failed"}:
            return "failed"
        return "partial"


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_chunk_doc_index"),
        UniqueConstraint(
            "id",
            "document_id",
            "course_id",
            name="uq_document_chunks_id_document_id_course_id",
        ),
        CheckConstraint(
            "page_number IS NULL OR page_number >= 1", name="page_number_positive"
        ),
        CheckConstraint(
            "(page_number IS NULL AND end_page_number IS NULL) OR "
            "(page_number IS NOT NULL AND end_page_number IS NOT NULL AND "
            "end_page_number >= page_number)",
            name="page_range_valid",
        ),
        CheckConstraint(
            "chunk_index = CAST(chunk_index AS INTEGER) AND chunk_index >= 0",
            name="chunk_index_nonnegative",
        ),
        ForeignKeyConstraint(
            ["document_id", "course_id"],
            ["uploaded_documents.id", "uploaded_documents.course_id"],
            name="fk_document_chunks_document_course_uploaded_documents",
            ondelete="CASCADE",
        ),
        Index(
            "ix_document_chunks_course_document_index",
            "course_id",
            "document_id",
            "chunk_index",
            "id",
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
    end_page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)

    text: Mapped[str] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now()
    )

    document: Mapped["UploadedDocument"] = relationship(
        back_populates="chunks", overlaps="course,chunks"
    )
    course: Mapped["Course"] = relationship(
        back_populates="chunks", overlaps="document,chunks"
    )
    embedding_record: Mapped["ChunkEmbedding | None"] = relationship(
        back_populates="chunk",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )


class ChunkEmbedding(Base):
    """The single current semantic vector for one current document chunk."""

    __tablename__ = "chunk_embeddings"
    __table_args__ = (
        UniqueConstraint("chunk_id", name="uq_chunk_embeddings_chunk_id"),
        CheckConstraint(
            f"dimensions = {EMBEDDING_DIMENSIONS}",
            name="dimensions_supported",
        ),
        CheckConstraint(
            "chunk_index = CAST(chunk_index AS INTEGER) AND chunk_index >= 0",
            name="chunk_index_nonnegative",
        ),
        CheckConstraint(
            f"length(trim(embedding_provider, '{_ASCII_WHITESPACE}')) > 0",
            name="embedding_provider_nonblank",
        ),
        CheckConstraint(
            f"length(trim(embedding_model, '{_ASCII_WHITESPACE}')) > 0",
            name="embedding_model_nonblank",
        ),
        ForeignKeyConstraint(
            ["chunk_id", "document_id", "course_id"],
            [
                "document_chunks.id",
                "document_chunks.document_id",
                "document_chunks.course_id",
            ],
            name="fk_chunk_embeddings_chunk_document_course_document_chunks",
            ondelete="CASCADE",
        ),
        Index(
            "ix_chunk_embeddings_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ).ddl_if(dialect="postgresql"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    chunk_id: Mapped[int] = mapped_column(Integer)
    document_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    course_id: Mapped[int] = mapped_column(Integer, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)

    embedding: Mapped[list[float]] = mapped_column(EmbeddingVector())
    embedding_provider: Mapped[str] = mapped_column(String(50))
    embedding_model: Mapped[str] = mapped_column(String(128))
    dimensions: Mapped[int] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now()
    )

    chunk: Mapped["DocumentChunk"] = relationship(back_populates="embedding_record")


class DocumentPage(Base):
    """Canonical raw and cleaned merged content for one document page."""

    __tablename__ = "document_pages"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "content_index",
            name="uq_document_pages_document_content_index",
        ),
        CheckConstraint("content_index >= 0", name="content_index_nonnegative"),
        CheckConstraint(
            "page_number IS NULL OR page_number >= 1", name="page_number_positive"
        ),
        CheckConstraint(
            "raw_extraction_method IS NULL OR "
            "raw_extraction_method IN ('native', 'decoded')",
            name="raw_extraction_method_valid",
        ),
        CheckConstraint(
            "extraction_method IS NULL OR "
            "extraction_method IN ('native', 'decoded', 'ocr')",
            name="extraction_method_valid",
        ),
        CheckConstraint(
            "NOT needs_ocr OR (page_number IS NOT NULL AND "
            "(has_images OR has_visual_content))",
            name="ocr_candidate_valid",
        ),
        CheckConstraint(
            "NOT raw_needs_ocr OR (page_number IS NOT NULL AND "
            "(has_images OR has_visual_content))",
            name="raw_ocr_candidate_valid",
        ),
        CheckConstraint(
            "ocr_status IN ('not_required', 'pending', 'succeeded', 'no_text')",
            name="ocr_status_valid",
        ),
        CheckConstraint(
            "visual_analysis_status IN "
            "('not_applicable', 'pending', 'not_configured', 'completed', "
            "'partial', 'failed')",
            name="visual_analysis_status_valid",
        ),
        ForeignKeyConstraint(
            ["document_id", "course_id"],
            ["uploaded_documents.id", "uploaded_documents.course_id"],
            name="fk_document_pages_document_course_uploaded_documents",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    content_index: Mapped[int] = mapped_column(Integer)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_text: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    raw_extraction_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    extraction_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    has_images: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    needs_ocr: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    raw_needs_ocr: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    ocr_status: Mapped[str] = mapped_column(
        String(20), default="not_required", server_default="not_required"
    )
    has_visual_content: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    visual_analysis_status: Mapped[str] = mapped_column(
        String(20), default="not_applicable", server_default="not_applicable"
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now()
    )

    document: Mapped["UploadedDocument"] = relationship(
        back_populates="pages", overlaps="course,document_pages"
    )
    course: Mapped["Course"] = relationship(
        back_populates="document_pages", overlaps="document,pages"
    )
    visuals: Mapped[list["DocumentVisual"]] = relationship(
        back_populates="page",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="DocumentVisual.visual_index",
    )


class DocumentVisual(Base):
    """One meaningful visual region detected on a physical PDF page."""

    __tablename__ = "document_visuals"
    __table_args__ = (
        UniqueConstraint("page_id", "visual_index", name="uq_visual_page_index"),
        CheckConstraint("visual_index >= 0", name="visual_index_nonnegative"),
        CheckConstraint(
            "visual_type IN "
            "('diagram', 'table', 'chart', 'screenshot', 'figure', 'flowchart', "
            "'other')",
            name="visual_type_valid",
        ),
        CheckConstraint("source IN ('image', 'table', 'drawing')", name="source_valid"),
        CheckConstraint(
            "bbox_x0 >= 0 AND bbox_y0 >= 0 AND bbox_x1 > bbox_x0 AND bbox_y1 > bbox_y0",
            name="bbox_valid",
        ),
        CheckConstraint(
            "analysis_status IN "
            "('pending', 'not_configured', 'succeeded', 'skipped', 'failed')",
            name="analysis_status_valid",
        ),
        CheckConstraint(
            "description IS NULL OR analysis_status = 'succeeded'",
            name="description_status_valid",
        ),
        CheckConstraint(
            "analysis_status <> 'failed' OR "
            f"(error_code IS NOT NULL AND "
            f"length(trim(error_code, '{_ASCII_WHITESPACE}')) > 0)",
            name="failed_error_code_required",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    page_id: Mapped[int] = mapped_column(
        ForeignKey("document_pages.id", ondelete="CASCADE"), index=True
    )
    visual_index: Mapped[int] = mapped_column(Integer)
    visual_type: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(20))
    bbox_x0: Mapped[float] = mapped_column(Float)
    bbox_y0: Mapped[float] = mapped_column(Float)
    bbox_x1: Mapped[float] = mapped_column(Float)
    bbox_y1: Mapped[float] = mapped_column(Float)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    analysis_status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending"
    )
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now()
    )

    page: Mapped["DocumentPage"] = relationship(back_populates="visuals")


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
            f"job_type = '{JOB_TYPE_EXTRACT_DOCUMENT}'", name="job_type_valid"
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
        CheckConstraint(
            "processing_stage IS NULL OR processing_stage IN "
            f"({_DOCUMENT_PROCESSING_STAGES_SQL})",
            name="processing_stage_valid",
        ),
        CheckConstraint(
            "failed_stage IS NULL OR failed_stage IN "
            f"({_DOCUMENT_PROCESSING_STAGES_SQL})",
            name="failed_stage_valid",
        ),
        CheckConstraint(
            "processing_stage IS NULL OR status = 'running'",
            name="processing_stage_status",
        ),
        CheckConstraint(
            "failed_stage IS NULL OR status = 'failed'",
            name="failed_stage_status",
        ),
        CheckConstraint(
            "status <> 'failed' OR (last_error_code IS NOT NULL AND "
            f"length(trim(last_error_code, '{_ASCII_WHITESPACE}')) > 0)",
            name="failed_error_code_nonblank",
        ),
        CheckConstraint(
            "status <> 'running' OR (lease_owner IS NOT NULL AND "
            f"length(trim(lease_owner, '{_ASCII_WHITESPACE}')) > 0)",
            name="running_lease_owner_nonblank",
        ),
        CheckConstraint(
            "status <> 'running' OR (claim_token IS NOT NULL AND "
            "length(claim_token) = 36)",
            name="running_claim_token_length",
        ),
        Index("ix_processing_jobs_claimable", "status", "available_at", "id"),
        Index("ix_processing_jobs_recoverable", "status", "lease_expires_at", "id"),
        Index("ix_processing_jobs_course_created", "course_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    course_id: Mapped[int] = mapped_column(Integer)
    job_type: Mapped[str] = mapped_column(String(50))
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
    processing_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    failed_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
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
    __table_args__ = (
        Index(
            "uq_generated_outputs_id_course_id",
            "id",
            "course_id",
            unique=True,
        ),
        Index(
            "ix_generated_outputs_user_course_created",
            "user_id",
            "course_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_generated_outputs_user_created",
            "user_id",
            "created_at",
            "id",
        ),
    )

    # The auto-numbered identity of this row. Every table gets one.
    id: Mapped[int] = mapped_column(primary_key=True)

    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )

    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    model_used: Mapped[str | None] = mapped_column(String(150), nullable=True)

    # What kind of output this is, stored as a short text label,
    # for example: "summary". Later kinds can be added without
    # changing the table structure.
    output_type: Mapped[str] = mapped_column(String(30))

    content: Mapped[str] = mapped_column(Text)  # Unlimited length

    generation_settings: Mapped[str | None] = mapped_column(Text, nullable=True)

    generation_context: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now()
    )

    # Navigation attribute (NOT a column): lets Python code write
    # output.course to reach the Course object. The partner attribute
    # on Course must be named exactly "generated_outputs".
    course: Mapped["Course"] = relationship(back_populates="generated_outputs")

    topic_candidates: Mapped[list["ExamTopicCandidate"]] = relationship(
        back_populates="analysis_output",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ExamTopicCandidate.position",
    )


class ExamTopicCandidate(Base):
    """One candidate exam topic discovered by one Exam Mode analysis run.

    Append-only: the application never updates a row here. A later analysis
    writes a new ``generated_outputs`` row and a fresh set of candidates, so an
    older exam plan can still be reopened against the evidence it was actually
    built from.

    ``course_id`` is denormalized for course-scoped reads and held true by the
    composite foreign key, the arrangement ``document_chunks`` and
    ``chunk_embeddings`` already use. Without it a row whose ``course_id``
    disagreed with its analysis would surface in another course's read, after
    the authorization boundary had already passed.

    ``topic_key`` is produced by ``services/exam_topics.canonical_topic_key``
    and is deliberately independent of display casing, so a mastery label the
    model never saw can still be matched against it at read time.
    """

    __tablename__ = "exam_topic_candidates"
    __table_args__ = (
        UniqueConstraint(
            "analysis_output_id",
            "topic_key",
            name="uq_exam_topic_candidates_analysis_output_id_topic_key",
        ),
        CheckConstraint("position >= 0", name="position_nonnegative"),
        CheckConstraint(
            f"length(trim(topic_key, '{_ASCII_WHITESPACE}')) > 0",
            name="topic_key_nonblank",
        ),
        CheckConstraint(
            f"length(trim(display_label, '{_ASCII_WHITESPACE}')) > 0",
            name="display_label_nonblank",
        ),
        CheckConstraint(
            "discovery_confidence >= 0 AND discovery_confidence <= 1",
            name="discovery_confidence_fraction",
        ),
        CheckConstraint(
            "syllabus_weight_percent IS NULL OR "
            "(syllabus_weight_percent >= 0 AND syllabus_weight_percent <= 100)",
            name="syllabus_weight_percent_range",
        ),
        CheckConstraint(
            "syllabus_mention_count >= 0 AND material_chunk_count >= 0 AND "
            "material_character_count >= 0 AND past_exam_question_count >= 0",
            name="evidence_counts_nonnegative",
        ),
        CheckConstraint(
            "past_exam_marks_total IS NULL OR past_exam_marks_total >= 0",
            name="past_exam_marks_total_nonnegative",
        ),
        CheckConstraint(
            "in_syllabus OR in_course_topics OR in_past_exams OR in_material",
            name="at_least_one_source",
        ),
        ForeignKeyConstraint(
            ["analysis_output_id", "course_id"],
            ["generated_outputs.id", "generated_outputs.course_id"],
            name="fk_exam_topic_candidates_analysis_course_generated_outputs",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_output_id: Mapped[int] = mapped_column(Integer, index=True)
    course_id: Mapped[int] = mapped_column(Integer, index=True)
    position: Mapped[int] = mapped_column(Integer)

    topic_key: Mapped[str] = mapped_column(String(120))
    display_label: Mapped[str] = mapped_column(String(200))
    aliases: Mapped[list | None] = mapped_column(JSON, nullable=True)

    in_syllabus: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    in_course_topics: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    in_past_exams: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    in_material: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )

    discovery_confidence: Mapped[float] = mapped_column(
        Float, default=0.5, server_default="0.5"
    )

    syllabus_weight_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    syllabus_mention_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    material_chunk_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    material_character_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    past_exam_question_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    past_exam_marks_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    past_exam_years: Mapped[list | None] = mapped_column(JSON, nullable=True)

    citations: Mapped[list | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now()
    )

    analysis_output: Mapped["GeneratedOutput"] = relationship(
        back_populates="topic_candidates"
    )


class PastExamQuestion(Base):
    """One question extracted from one past exam paper.

    A question belongs to the paper it was printed in, not to whichever
    analysis happened to read that paper. It is extracted once, when the
    document is uploaded, and every later analysis reads these same rows: a
    rescan re-extracts nothing, and two analyses of one paper cannot disagree
    about what it asks.

    ``document_id`` is therefore mandatory. A composite foreign key carries
    ``course_id`` with it, so a question can never be attributed to a paper
    belonging to another course, and deleting the paper retracts its questions.

    Visual references are stored as the stable ``page_number`` /
    ``visual_index`` descriptor rather than a ``document_visuals`` identifier,
    because reprocessing a document deletes and reinserts its pages and those
    identifiers do not survive it.

    ``topic_key`` is computed by ``services/exam_topics.canonical_topic_key``
    from the label the extractor gave the question, so extraction needs to know
    nothing about any analysis. An analysis resolves those keys against its own
    candidates later, exactly as it already does for mastery labels.
    """

    __tablename__ = "past_exam_questions"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "position",
            name="uq_past_exam_questions_document_id_position",
        ),
        CheckConstraint("position >= 0", name="position_nonnegative"),
        CheckConstraint(
            f"length(trim(question_text, '{_ASCII_WHITESPACE}')) > 0",
            name="question_text_nonblank",
        ),
        CheckConstraint(
            "page_start IS NULL OR page_start >= 1", name="page_start_positive"
        ),
        CheckConstraint(
            "(page_start IS NULL AND page_end IS NULL) OR "
            "(page_start IS NOT NULL AND page_end IS NOT NULL AND "
            "page_end >= page_start)",
            name="page_range_valid",
        ),
        CheckConstraint(
            "question_number IS NULL OR question_number >= 0",
            name="question_number_nonnegative",
        ),
        CheckConstraint("marks IS NULL OR marks >= 0", name="marks_nonnegative"),
        CheckConstraint(
            f"question_type IN ({_EXAM_QUESTION_TYPES_SQL})",
            name="question_type_valid",
        ),
        CheckConstraint(
            f"difficulty IS NULL OR difficulty IN ({_EXAM_QUESTION_DIFFICULTIES_SQL})",
            name="difficulty_valid",
        ),
        ForeignKeyConstraint(
            ["document_id", "course_id"],
            ["uploaded_documents.id", "uploaded_documents.course_id"],
            name="fk_past_exam_questions_document_course_uploaded_documents",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    course_id: Mapped[int] = mapped_column(Integer, index=True)
    position: Mapped[int] = mapped_column(Integer)

    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)

    question_label: Mapped[str | None] = mapped_column(String(50), nullable=True)
    question_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    question_text: Mapped[str] = mapped_column(Text)
    subparts: Mapped[list | None] = mapped_column(JSON, nullable=True)

    question_type: Mapped[str] = mapped_column(
        String(30), default="unspecified", server_default="unspecified"
    )
    difficulty: Mapped[str | None] = mapped_column(String(10), nullable=True)
    marks: Mapped[float | None] = mapped_column(Float, nullable=True)

    answer_guidance: Mapped[str | None] = mapped_column(Text, nullable=True)
    marking_points: Mapped[list | None] = mapped_column(JSON, nullable=True)

    visual_refs: Mapped[list | None] = mapped_column(JSON, nullable=True)

    topic_key: Mapped[str | None] = mapped_column(
        String(120), nullable=True, index=True
    )
    topic_mappings: Mapped[list | None] = mapped_column(JSON, nullable=True)

    citations: Mapped[list | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now()
    )

    document: Mapped["UploadedDocument"] = relationship(
        back_populates="past_exam_questions",
        overlaps="course,past_exam_questions",
    )


class ExamTopicUnlock(Base):
    """One student's paid access to everything Exam Mode makes for one topic.

    Exam Mode charges per topic, not per artifact: unlocking a topic buys its
    guide, its summary, its practice questions, its topic exam, and its similar
    questions together, and the charge lands the first time the student asks
    for any of them rather than up front for a plan they may not finish.

    The unique key is ``(course_id, user_id, topic_key)`` and nothing else,
    which is what makes a regenerated plan over the same topics free: the row
    outlives the plan that first named the topic.

    ``credit_transaction_id`` is null when the account was not metered. That is
    "no credit moved", not an unfinished row, and it is the same distinction
    ``ChargeReceipt.is_exempt`` draws one layer up.
    """

    __tablename__ = "exam_topic_unlocks"
    __table_args__ = (
        UniqueConstraint(
            "course_id",
            "user_id",
            "topic_key",
            name="uq_exam_topic_unlocks_course_id_user_id_topic_key",
        ),
        CheckConstraint(
            f"length(trim(topic_key, '{_ASCII_WHITESPACE}')) > 0",
            name="topic_key_nonblank",
        ),
        CheckConstraint("amount >= 0", name="amount_nonnegative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    topic_key: Mapped[str] = mapped_column(String(120), index=True)

    credit_transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("credit_transactions.id", ondelete="SET NULL"), nullable=True
    )

    amount: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now()
    )


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint(
            f"conversation_type IN ({_CONVERSATION_TYPES_SQL})",
            name="conversation_type_valid",
        ),
        Index(
            "ix_conversations_user_course_updated",
            "user_id",
            "course_id",
            "updated_at",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )

    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"),
        index=True,
    )

    conversation_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped["User"] = relationship(
        back_populates="conversations",
    )

    course: Mapped["Course"] = relationship(
        back_populates="conversations",
    )

    messages: Mapped[list["ConversationMessage"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ConversationMessage.id",
    )


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant')",
            name="conversation_message_role_valid",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
    )

    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    citations: Mapped[list | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=func.now(),
    )

    conversation: Mapped["Conversation"] = relationship(
        back_populates="messages",
    )


class Quiz(Base):
    """A quiz that belongs to one course."""

    __tablename__ = "quizzes"

    __table_args__ = (
        # A unique index rather than a constraint, so SQLite adds it without
        # rebuilding the table. Null is distinct on both engines, which is what
        # lets every quiz generated without a request identifier coexist.
        Index(
            "uq_quizzes_course_id_user_id_generation_request_id",
            "course_id",
            "user_id",
            "generation_request_id",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # Owner course. Same cascade logic.

    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )

    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    title: Mapped[str] = mapped_column(String(200))

    quiz_type: Mapped[str] = mapped_column(
        String(50), default="standard", server_default="standard"
    )

    model_used: Mapped[str | None] = mapped_column(String(150), nullable=True)

    generation_settings: Mapped[str | None] = mapped_column(Text, nullable=True)

    generation_context: Mapped[str | None] = mapped_column(Text, nullable=True)

    # What this quiz is for. Null is a quiz that predates Exam Mode; nothing
    # back-fills it, because "practice" would be a claim about rows nobody
    # classified.
    purpose: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)

    exam_plan_output_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )

    exam_topic_key: Mapped[str | None] = mapped_column(
        String(120), nullable=True, index=True
    )

    # How long a sitting of this quiz may last. Null is an untimed quiz, which
    # is every quiz that predates timed mock exams; a positive value is what
    # makes the ordinary attempt endpoint insist on a server-owned session
    # instead of trusting a clock the client controls.
    time_limit_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # The client's own identifier for the request that produced this quiz, so a
    # retry after a timeout returns the quiz it already paid for. Null for every
    # quiz generated without one, and null is distinct in a unique index on both
    # engines, so no back-fill is needed and existing rows are unaffected.
    generation_request_id: Mapped[str | None] = mapped_column(
        String(MAX_GENERATION_REQUEST_ID_CHARS), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now()
    )

    # Navigation up to the course
    course: Mapped["Course"] = relationship(back_populates="quizzes")

    user: Mapped["User | None"] = relationship(back_populates="quizzes")

    questions: Mapped[list["QuizQuestion"]] = relationship(
        back_populates="quiz", cascade="all, delete-orphan", passive_deletes=True
    )

    attempts: Mapped[list["QuizAttempt"]] = relationship(
        back_populates="quiz", cascade="all, delete-orphan", passive_deletes=True
    )

    sessions: Mapped[list["QuizSession"]] = relationship(
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
        CheckConstraint(
            "question_type IN ('multiple_choice', 'true_false', 'short_answer', 'open_ended')",
            name="quiz_question_type_valid",
        ),
        CheckConstraint(
            "question_index = CAST(question_index AS INTEGER) AND question_index >= 0",
            name="question_index_nonnegative",
        ),
        CheckConstraint(
            "correct_option_index IS NULL OR "
            "(correct_option_index = CAST(correct_option_index AS INTEGER) "
            "AND correct_option_index >= 0)",
            name="correct_option_index_nonnegative",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    quiz_id: Mapped[int] = mapped_column(
        ForeignKey("quizzes.id", ondelete="CASCADE"), index=True
    )

    # The position of this question inside its quiz: 0,1,2,3...
    question_index: Mapped[int] = mapped_column(Integer)

    question_type: Mapped[str] = mapped_column(
        String(30),
        default=QUESTION_TYPE_MULTIPLE_CHOICE,
        server_default=QUESTION_TYPE_MULTIPLE_CHOICE,
    )

    # The question text itself
    question_text: Mapped[str] = mapped_column(Text)

    # The answer options stored as one JSON value, for example:
    # ["Paris", "London", "Berlin", "Madrid"]. We chose JSON instead
    # of a separate options table because options are only ever read
    # and written together as one bundle
    options: Mapped[list | None] = mapped_column(JSON, nullable=True)

    correct_option_index: Mapped[int | None] = mapped_column(Integer, nullable=True)

    difficulty: Mapped[str | None] = mapped_column(String(10), nullable=True)

    correct_answer: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    citations: Mapped[list | None] = mapped_column(JSON, nullable=True)

    topic: Mapped[str | None] = mapped_column(String(200), nullable=True)

    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)

    # The past exam question this one was written in the mould of, when it was
    # written that way. No foreign key, for the reason exam_plan_output_id has
    # none: constraining a column on an existing table forces a SQLite rebuild.
    # It is also the safer direction here -- a cascade would delete a quiz a
    # student had already sat when its source paper was removed, and
    # re-extraction replaces a paper's questions wholesale. A pointer that no
    # longer resolves means the original is gone, which readers must handle in
    # any case; the generated question keeps its own denormalized citations,
    # exactly as a citation outlives the document it came from.
    source_past_exam_question_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )

    quiz: Mapped["Quiz"] = relationship(back_populates="questions")

    answers: Mapped[list["QuizAttemptAnswer"]] = relationship(
        back_populates="question", cascade="all, delete-orphan", passive_deletes=True
    )

    session_answers: Mapped[list["QuizSessionAnswer"]] = relationship(
        back_populates="question", cascade="all, delete-orphan", passive_deletes=True
    )


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
        Index(
            "ix_quiz_attempts_quiz_user_created",
            "quiz_id",
            "user_id",
            "created_at",
            "id",
        ),
        Index("ix_quiz_attempts_user_created", "user_id", "created_at", "id"),
        Index("ix_quiz_attempts_quiz_created", "quiz_id", "created_at", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # Parent one: who attempted. User deleted -> attempts deleted.
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    # Parent two: which quiz. Quiz deleted -> attempts deleted.
    quiz_id: Mapped[int] = mapped_column(ForeignKey("quizzes.id", ondelete="CASCADE"))

    # The result as a fraction between 0.0 and 1.0. Float is the
    # column type for decimal numbers.
    score: Mapped[float] = mapped_column(Float)

    time_spent_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="quiz_attempts")
    quiz: Mapped["Quiz"] = relationship(back_populates="attempts")

    answers: Mapped[list["QuizAttemptAnswer"]] = relationship(
        back_populates="attempt", cascade="all, delete-orphan", passive_deletes=True
    )

    session: Mapped["QuizSession | None"] = relationship(
        back_populates="attempt",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class QuizAttemptAnswer(Base):
    """One answer a user gave to one question inside one attempt."""

    __tablename__ = "quiz_attempt_answers"
    __table_args__ = (
        UniqueConstraint(
            "attempt_id", "quiz_question_id", name="uq_attempt_answer_question"
        ),
        CheckConstraint(
            "selected_option_index IS NULL OR selected_option_index >= 0",
            name="selected_option_index_nonnegative",
        ),
        CheckConstraint(
            "time_spent_seconds IS NULL OR time_spent_seconds >= 0",
            name="answer_time_spent_nonnegative",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    attempt_id: Mapped[int] = mapped_column(
        ForeignKey("quiz_attempts.id", ondelete="CASCADE"), index=True
    )

    quiz_question_id: Mapped[int] = mapped_column(
        ForeignKey("quiz_questions.id", ondelete="CASCADE"), index=True
    )

    selected_option_index: Mapped[int | None] = mapped_column(Integer, nullable=True)

    text_response: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    score: Mapped[float | None] = mapped_column(Float, nullable=True)

    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)

    time_spent_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    topic: Mapped[str | None] = mapped_column(String(200), nullable=True)

    grading_status: Mapped[str] = mapped_column(
        String(20), default="not_required", server_default="not_required"
    )

    grading_model: Mapped[str | None] = mapped_column(String(150), nullable=True)

    attempt: Mapped["QuizAttempt"] = relationship(back_populates="answers")
    question: Mapped["QuizQuestion"] = relationship(back_populates="answers")


class QuizSession(Base):
    """One student's timed sitting of one quiz.

    The server owns ``started_at`` and ``expires_at``. The client is told the
    deadline and never sets it, because a timer a candidate can edit is not a
    timer. Expiry is a comparison against ``expires_at`` in the statement that
    reads the row, the way ``processing_jobs.lease_expires_at`` is, so nothing
    has to be scheduled and a read can report a sitting as over before any
    write has caught up to saying so.

    ``attempt_id`` is what makes a second submission impossible to represent:
    it is unique, and ``submitted_state_valid`` forbids a submitted row without
    one. A guarded update is what wins the race between two submissions; these
    constraints are what stop a bug from recording the outcome twice.

    An expired sitting is still submittable. The student already spent the time
    and the answers were already saved, so 'expired' is a statement about the
    deadline rather than a terminal state, and ``expired_at`` survives the move
    to 'submitted'.
    """

    __tablename__ = "quiz_sessions"
    __table_args__ = (
        UniqueConstraint("attempt_id", name="uq_quiz_sessions_attempt_id"),
        CheckConstraint(
            f"status IN ({_QUIZ_SESSION_STATUSES_SQL})", name="status_valid"
        ),
        CheckConstraint(
            "time_limit_seconds > 0 AND "
            f"time_limit_seconds <= {MAX_QUIZ_TIME_LIMIT_SECONDS}",
            name="time_limit_seconds_bounded",
        ),
        CheckConstraint("expires_at > started_at", name="expires_after_start"),
        CheckConstraint(
            "(status = 'submitted' AND submitted_at IS NOT NULL "
            "AND attempt_id IS NOT NULL) OR "
            "(status <> 'submitted' AND submitted_at IS NULL "
            "AND attempt_id IS NULL)",
            name="submitted_state_valid",
        ),
        CheckConstraint(
            "(status = 'active' AND expired_at IS NULL) OR "
            "(status = 'expired' AND expired_at IS NOT NULL) OR "
            "status = 'submitted'",
            name="expired_state_valid",
        ),
        # One live sitting per student per quiz, so a reloaded page rejoins the
        # timer it already started instead of quietly starting a second one and
        # splitting the drafts between them.
        Index(
            "uq_quiz_sessions_active_quiz_user",
            "quiz_id",
            "user_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
        Index("ix_quiz_sessions_expirable", "status", "expires_at", "id"),
        Index("ix_quiz_sessions_user_quiz_started", "user_id", "quiz_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    quiz_id: Mapped[int] = mapped_column(
        ForeignKey("quizzes.id", ondelete="CASCADE"), index=True
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    attempt_id: Mapped[int | None] = mapped_column(
        ForeignKey("quiz_attempts.id", ondelete="CASCADE"), nullable=True
    )

    status: Mapped[str] = mapped_column(
        String(20),
        default=QUIZ_SESSION_STATUS_ACTIVE,
        server_default=QUIZ_SESSION_STATUS_ACTIVE,
    )

    # Frozen when the sitting starts, so editing the quiz afterwards cannot
    # lengthen or shorten an examination already under way.
    time_limit_seconds: Mapped[int] = mapped_column(Integer)

    started_at: Mapped[datetime] = mapped_column(UTCDateTime())

    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())

    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    expired_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now()
    )

    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now()
    )

    quiz: Mapped["Quiz"] = relationship(back_populates="sessions")
    user: Mapped["User"] = relationship(back_populates="quiz_sessions")
    attempt: Mapped["QuizAttempt | None"] = relationship(back_populates="session")

    answers: Mapped[list["QuizSessionAnswer"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True
    )


class QuizSessionAnswer(Base):
    """One draft answer inside a sitting, overwritten in place as it changes.

    The unique key on (session, question) is what makes saving a draft an
    upsert rather than an append: there is one current answer per question and
    the history of edits is deliberately not kept.

    Drafts are never deleted when a sitting expires. They are the reason
    expiry does not cost a student their work: the deadline stops new writes,
    it does not discard the ones that already landed.
    """

    __tablename__ = "quiz_session_answers"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "quiz_question_id",
            name="uq_quiz_session_answers_session_question",
        ),
        CheckConstraint(
            "selected_option_index IS NULL OR selected_option_index >= 0",
            name="selected_option_index_nonnegative",
        ),
        CheckConstraint(
            "time_spent_seconds IS NULL OR time_spent_seconds >= 0",
            name="answer_time_spent_nonnegative",
        ),
        # An answer is a selection or a piece of writing, never both. The same
        # rule the attempt validator enforces, made a fact of the schema so a
        # malformed draft cannot survive long enough to reach grading.
        CheckConstraint(
            "NOT (selected_option_index IS NOT NULL AND text_response IS NOT NULL)",
            name="answer_form_exclusive",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    session_id: Mapped[int] = mapped_column(
        ForeignKey("quiz_sessions.id", ondelete="CASCADE"), index=True
    )

    quiz_question_id: Mapped[int] = mapped_column(
        ForeignKey("quiz_questions.id", ondelete="CASCADE"), index=True
    )

    selected_option_index: Mapped[int | None] = mapped_column(Integer, nullable=True)

    text_response: Mapped[str | None] = mapped_column(Text, nullable=True)

    time_spent_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now()
    )

    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now()
    )

    session: Mapped["QuizSession"] = relationship(back_populates="answers")
    question: Mapped["QuizQuestion"] = relationship(back_populates="session_answers")


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
        CheckConstraint(
            "quizzes_completed >= 0",
            name="quizzes_completed_nonnegative",
        ),
        CheckConstraint(
            "correct_answers_count >= 0",
            name="correct_answers_count_nonnegative",
        ),
        CheckConstraint(
            "incorrect_answers_count >= 0",
            name="incorrect_answers_count_nonnegative",
        ),
        CheckConstraint(
            "total_questions_answered >= 0",
            name="total_questions_answered_nonnegative",
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

    quizzes_completed: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )

    correct_answers_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )

    incorrect_answers_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )

    total_questions_answered: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )

    weak_topics: Mapped[list | None] = mapped_column(JSON, nullable=True)

    quiz_history: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # SQLAlchemy includes the onupdate expression in ORM-generated updates.
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now()
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
        UTCDateTime(), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="knowledge_items")

    embedding_record: Mapped["ProfileKnowledgeEmbedding | None"] = relationship(
        back_populates="knowledge",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )


class ProfileKnowledgeEmbedding(Base):
    __tablename__ = "profile_knowledge_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "knowledge_id", name="uq_profile_knowledge_embeddings_knowledge_id"
        ),
        CheckConstraint(
            f"dimensions = {EMBEDDING_DIMENSIONS}",
            name="dimensions_supported",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    knowledge_id: Mapped[int] = mapped_column(
        ForeignKey("profile_knowledge.id", ondelete="CASCADE"), index=True
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    embedding: Mapped[list[float]] = mapped_column(EmbeddingVector())
    embedding_provider: Mapped[str] = mapped_column(String(50))
    embedding_model: Mapped[str] = mapped_column(String(128))
    dimensions: Mapped[int] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now()
    )

    knowledge: Mapped["ProfileKnowledge"] = relationship(
        back_populates="embedding_record"
    )


class AiUsageLog(Base):
    """Structured, privacy-safe record of AI model generation activity.

    Stores operational telemetry (token counts, latency, status, error category)
    without persisting any raw prompt, chunk text, response content, or secrets.
    """

    __tablename__ = "ai_usage_logs"
    __table_args__ = (
        CheckConstraint(
            "estimated_cost_usd IS NULL OR "
            "(estimated_cost_usd >= 0 AND estimated_cost_usd <= 1000000)",
            name="ck_ai_usage_logs_estimated_cost_range",
        ),
        CheckConstraint(
            "(estimated_cost_usd IS NULL AND pricing_version IS NULL) OR "
            "(estimated_cost_usd IS NOT NULL AND pricing_version IS NOT NULL)",
            name="ck_ai_usage_logs_pricing_pair",
        ),
        Index("ix_ai_usage_logs_user_created", "user_id", "created_at"),
        Index("ix_ai_usage_logs_course_created", "course_id", "created_at"),
        Index("ix_ai_usage_logs_type_created", "generation_type", "created_at"),
        Index("ix_ai_usage_logs_success_created", "success", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    course_id: Mapped[int | None] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=True, index=True
    )
    generation_type: Mapped[str] = mapped_column(String(50), index=True)
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(128))
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean)
    error_category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    pricing_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="ai_usage_logs")
    course: Mapped["Course | None"] = relationship(back_populates="ai_usage_logs")


class CreditTransaction(Base):
    """Immutable accounting event explaining one change to a credit balance.

    The ledger is append-only: a mistake is corrected by a new, opposing
    transaction, never by editing or deleting an existing row. For every
    account with a non-null balance, ``users.credits`` equals the sum of that
    account's deltas. Accounts with a null balance are not metered; retained
    history remains immutable across role transitions. See docs/credits.md.
    """

    __tablename__ = "credit_transactions"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "grant_period",
            name="uq_credit_transactions_user_grant_period",
        ),
        UniqueConstraint(
            "refunds_transaction_id",
            name="uq_credit_transactions_refunds_transaction_id",
        ),
        Index("ix_credit_transactions_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    delta: Mapped[float] = mapped_column(Float)
    balance_after: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(40))
    actor_type: Mapped[str] = mapped_column(String(20))
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    refunds_transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("credit_transactions.id", ondelete="CASCADE"), nullable=True
    )
    grant_period: Mapped[str | None] = mapped_column(String(7), nullable=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now()
    )

    user: Mapped["User"] = relationship(
        back_populates="credit_transactions", foreign_keys=[user_id]
    )


class RateLimitBucket(Base):
    """One fixed-window counter for one abuse-control key.

    ``key`` already encodes the dimension being limited (for example
    ``login:ip:203.0.113.4`` or ``generation:user:42:quiz``), so one row per key
    is enough: a window rollover resets ``count`` in place rather than inserting
    a new row, keeping the table's size bounded by the number of active keys
    rather than growing with request volume. ``utils/rate_limit.py`` is the only
    module that reads or writes this table. See docs/rate_limiting.md.
    """

    __tablename__ = "rate_limit_buckets"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(UTCDateTime())
    count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    violation_streak: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class FlashcardSet(Base):
    __tablename__ = "flashcard_sets"

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now()
    )


class Flashcard(Base):
    __tablename__ = "flashcards"

    id: Mapped[int] = mapped_column(primary_key=True)
    set_id: Mapped[int] = mapped_column(
        ForeignKey("flashcard_sets.id", ondelete="CASCADE"), index=True
    )
    front_text: Mapped[str] = mapped_column(Text)
    back_text: Mapped[str] = mapped_column(Text)
    topic: Mapped[str | None] = mapped_column(String(200), nullable=True)
    citations: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now()
    )


class SpacedRepetitionState(Base):
    __tablename__ = "spaced_repetition_states"
    __table_args__ = (
        UniqueConstraint("user_id", "flashcard_id", name="uq_srs_user_flashcard"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    flashcard_id: Mapped[int] = mapped_column(
        ForeignKey("flashcards.id", ondelete="CASCADE"), index=True
    )
    interval_days: Mapped[float] = mapped_column(Float, default=0.0)
    ease_factor: Mapped[float] = mapped_column(Float, default=2.5)
    review_count: Mapped[int] = mapped_column(Integer, default=0)
    next_review_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    last_reviewed_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )


class ExamPlan(Base):
    __tablename__ = "exam_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    target_exam_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    plan_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now()
    )


class ProfileDocument(Base):
    __tablename__ = "profile_documents"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "file_hash",
            name="uq_profile_documents_user_id_file_hash",
        ),
        UniqueConstraint(
            "id",
            "user_id",
            name="uq_profile_documents_id_user_id",
        ),
        CheckConstraint("length(file_hash) = 64", name="profile_doc_file_hash_length"),
        CheckConstraint("file_size >= 0", name="profile_doc_file_size_nonnegative"),
        CheckConstraint(
            "status IN ('uploaded', 'processing', 'ready', 'failed', 'deleting')",
            name="profile_doc_status_valid",
        ),
        Index(
            "uq_profile_documents_storage_provider_storage_key",
            "storage_provider",
            "storage_key",
            unique=True,
        ),
        Index(
            "ix_profile_documents_user_status_created",
            "user_id",
            "status",
            "created_at",
            "id",
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
    storage_provider: Mapped[str] = mapped_column(String(50))
    storage_key: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(
        String(20), default="uploaded", server_default="uploaded"
    )
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="profile_documents")
    chunks: Mapped[list["ProfileDocumentChunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ProfileDocumentChunk.chunk_index",
    )
    pages: Mapped[list["ProfileDocumentPage"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ProfileDocumentPage.content_index",
    )
    processing_jobs: Mapped[list["ProfileProcessingJob"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class ProfileDocumentChunk(Base):
    __tablename__ = "profile_document_chunks"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "chunk_index", name="uq_profile_chunk_doc_index"
        ),
        UniqueConstraint(
            "id",
            "document_id",
            "user_id",
            name="uq_profile_document_chunks_id_doc_user",
        ),
        CheckConstraint(
            "page_number IS NULL OR page_number >= 1",
            name="profile_chunk_page_number_positive",
        ),
        CheckConstraint(
            "(page_number IS NULL AND end_page_number IS NULL) OR "
            "(page_number IS NOT NULL AND end_page_number IS NOT NULL AND "
            "end_page_number >= page_number)",
            name="profile_chunk_page_range_valid",
        ),
        CheckConstraint(
            "chunk_index = CAST(chunk_index AS INTEGER) AND chunk_index >= 0",
            name="profile_chunk_index_nonnegative",
        ),
        ForeignKeyConstraint(
            ["document_id", "user_id"],
            ["profile_documents.id", "profile_documents.user_id"],
            name="fk_profile_document_chunks_doc_user",
            ondelete="CASCADE",
        ),
        Index(
            "ix_profile_document_chunks_user_doc_index",
            "user_id",
            "document_id",
            "chunk_index",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now()
    )

    document: Mapped["ProfileDocument"] = relationship(back_populates="chunks")
    embedding_record: Mapped["ProfileChunkEmbedding | None"] = relationship(
        back_populates="chunk",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )


class ProfileChunkEmbedding(Base):
    __tablename__ = "profile_chunk_embeddings"
    __table_args__ = (
        UniqueConstraint("chunk_id", name="uq_profile_chunk_embeddings_chunk_id"),
        CheckConstraint(
            f"dimensions = {EMBEDDING_DIMENSIONS}",
            name="profile_dimensions_supported",
        ),
        CheckConstraint(
            "chunk_index = CAST(chunk_index AS INTEGER) AND chunk_index >= 0",
            name="profile_chunk_emb_index_nonnegative",
        ),
        CheckConstraint(
            f"length(trim(embedding_provider, '{_ASCII_WHITESPACE}')) > 0",
            name="profile_emb_provider_nonblank",
        ),
        CheckConstraint(
            f"length(trim(embedding_model, '{_ASCII_WHITESPACE}')) > 0",
            name="profile_emb_model_nonblank",
        ),
        ForeignKeyConstraint(
            ["chunk_id", "document_id", "user_id"],
            [
                "profile_document_chunks.id",
                "profile_document_chunks.document_id",
                "profile_document_chunks.user_id",
            ],
            name="fk_profile_chunk_embeddings_chunk_doc_user",
            ondelete="CASCADE",
        ),
        Index(
            "ix_profile_chunk_embeddings_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ).ddl_if(dialect="postgresql"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chunk_id: Mapped[int] = mapped_column(Integer)
    document_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list[float]] = mapped_column(EmbeddingVector())
    embedding_provider: Mapped[str] = mapped_column(String(50))
    embedding_model: Mapped[str] = mapped_column(String(128))
    dimensions: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now()
    )

    chunk: Mapped["ProfileDocumentChunk"] = relationship(
        back_populates="embedding_record"
    )


class ProfileDocumentPage(Base):
    __tablename__ = "profile_document_pages"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "content_index",
            name="uq_profile_document_pages_document_content_index",
        ),
        CheckConstraint("content_index >= 0", name="profile_content_index_nonnegative"),
        CheckConstraint(
            "page_number IS NULL OR page_number >= 1",
            name="profile_page_number_positive",
        ),
        CheckConstraint(
            "raw_extraction_method IS NULL OR "
            "raw_extraction_method IN ('native', 'decoded')",
            name="profile_raw_extraction_method_valid",
        ),
        CheckConstraint(
            "extraction_method IS NULL OR "
            "extraction_method IN ('native', 'decoded', 'ocr')",
            name="profile_extraction_method_valid",
        ),
        CheckConstraint(
            "NOT needs_ocr OR (page_number IS NOT NULL AND "
            "(has_images OR has_visual_content))",
            name="profile_ocr_candidate_valid",
        ),
        CheckConstraint(
            "NOT raw_needs_ocr OR (page_number IS NOT NULL AND "
            "(has_images OR has_visual_content))",
            name="profile_raw_ocr_candidate_valid",
        ),
        CheckConstraint(
            "ocr_status IN ('not_required', 'pending', 'succeeded', 'no_text')",
            name="profile_ocr_status_valid",
        ),
        CheckConstraint(
            "visual_analysis_status IN "
            "('not_applicable', 'pending', 'not_configured', 'completed', "
            "'partial', 'failed')",
            name="profile_visual_analysis_status_valid",
        ),
        ForeignKeyConstraint(
            ["document_id", "user_id"],
            ["profile_documents.id", "profile_documents.user_id"],
            name="fk_profile_document_pages_doc_user",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    content_index: Mapped[int] = mapped_column(Integer)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_text: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    raw_extraction_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    extraction_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    has_images: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    needs_ocr: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    raw_needs_ocr: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    ocr_status: Mapped[str] = mapped_column(
        String(20), default="not_required", server_default="not_required"
    )
    has_visual_content: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    visual_analysis_status: Mapped[str] = mapped_column(
        String(20), default="not_applicable", server_default="not_applicable"
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now()
    )

    document: Mapped["ProfileDocument"] = relationship(back_populates="pages")
    visuals: Mapped[list["ProfileDocumentVisual"]] = relationship(
        back_populates="page",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ProfileDocumentVisual.visual_index",
    )


class ProfileDocumentVisual(Base):
    __tablename__ = "profile_document_visuals"
    __table_args__ = (
        UniqueConstraint(
            "page_id", "visual_index", name="uq_profile_visual_page_index"
        ),
        CheckConstraint("visual_index >= 0", name="profile_visual_index_nonnegative"),
        CheckConstraint(
            "visual_type IN "
            "('diagram', 'table', 'chart', 'screenshot', 'figure', 'flowchart', "
            "'other')",
            name="profile_visual_type_valid",
        ),
        CheckConstraint(
            "source IN ('image', 'table', 'drawing')",
            name="profile_visual_source_valid",
        ),
        CheckConstraint(
            "bbox_x0 >= 0 AND bbox_y0 >= 0 AND bbox_x1 > bbox_x0 AND bbox_y1 > bbox_y0",
            name="profile_bbox_valid",
        ),
        CheckConstraint(
            "analysis_status IN "
            "('pending', 'not_configured', 'succeeded', 'skipped', 'failed')",
            name="profile_analysis_status_valid",
        ),
        CheckConstraint(
            "description IS NULL OR analysis_status = 'succeeded'",
            name="profile_description_status_valid",
        ),
        CheckConstraint(
            "analysis_status <> 'failed' OR "
            f"(error_code IS NOT NULL AND "
            f"length(trim(error_code, '{_ASCII_WHITESPACE}')) > 0)",
            name="profile_failed_error_code_required",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    page_id: Mapped[int] = mapped_column(
        ForeignKey("profile_document_pages.id", ondelete="CASCADE"), index=True
    )
    visual_index: Mapped[int] = mapped_column(Integer)
    visual_type: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(20))
    bbox_x0: Mapped[float] = mapped_column(Float)
    bbox_y0: Mapped[float] = mapped_column(Float)
    bbox_x1: Mapped[float] = mapped_column(Float)
    bbox_y1: Mapped[float] = mapped_column(Float)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    analysis_status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending"
    )
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now()
    )

    page: Mapped["ProfileDocumentPage"] = relationship(back_populates="visuals")


class ProfileProcessingJob(Base):
    __tablename__ = "profile_processing_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "user_id"],
            ["profile_documents.id", "profile_documents.user_id"],
            name="fk_profile_processing_jobs_doc_user",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "document_id", "job_type", name="uq_profile_processing_jobs_doc_type"
        ),
        CheckConstraint(
            f"job_type = '{JOB_TYPE_EXTRACT_DOCUMENT}'", name="profile_job_type_valid"
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="profile_job_status_valid",
        ),
        CheckConstraint("attempt_count >= 0", name="profile_attempt_count_nonnegative"),
        CheckConstraint("max_attempts > 0", name="profile_max_attempts_positive"),
        CheckConstraint(
            "attempt_count <= max_attempts", name="profile_attempt_count_within_limit"
        ),
        CheckConstraint(
            "status <> 'queued' OR attempt_count < max_attempts",
            name="profile_queued_attempts_available",
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
            name="profile_lease_state_valid",
        ),
        CheckConstraint(
            "(status IN ('succeeded', 'failed') AND finished_at IS NOT NULL) OR "
            "(status IN ('queued', 'running') AND finished_at IS NULL)",
            name="profile_finished_state_valid",
        ),
        CheckConstraint(
            "processing_stage IS NULL OR processing_stage IN "
            f"({_DOCUMENT_PROCESSING_STAGES_SQL})",
            name="profile_processing_stage_valid",
        ),
        CheckConstraint(
            "failed_stage IS NULL OR failed_stage IN "
            f"({_DOCUMENT_PROCESSING_STAGES_SQL})",
            name="profile_failed_stage_valid",
        ),
        CheckConstraint(
            "processing_stage IS NULL OR status = 'running'",
            name="profile_processing_stage_status",
        ),
        CheckConstraint(
            "failed_stage IS NULL OR status = 'failed'",
            name="profile_failed_stage_status",
        ),
        CheckConstraint(
            "status <> 'failed' OR (last_error_code IS NOT NULL AND "
            f"length(trim(last_error_code, '{_ASCII_WHITESPACE}')) > 0)",
            name="profile_failed_last_error_code_present",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        index=True,
    )
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    job_type: Mapped[str] = mapped_column(
        String(50),
        default=JOB_TYPE_EXTRACT_DOCUMENT,
        server_default=JOB_TYPE_EXTRACT_DOCUMENT,
    )
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20),
        default=JOB_STATUS_QUEUED,
        server_default=JOB_STATUS_QUEUED,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
    )
    max_attempts: Mapped[int] = mapped_column(Integer)
    available_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=func.now(),
    )
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
    processing_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    failed_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=func.now(),
        onupdate=func.now(),
    )

    document: Mapped["ProfileDocument"] = relationship(back_populates="processing_jobs")
