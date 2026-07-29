from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)

from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)

    users: Mapped[list["User"]] = relationship(back_populates="role")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    role: Mapped["Role"] = relationship(back_populates="users")

    courses: Mapped[list["Course"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan", passive_deletes=True
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

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
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
        back_populates="course", cascade="all, delete-orphan", passive_deletes=True
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

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )

    original_filename: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="uploaded")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    course: Mapped["Course"] = relationship(back_populates="documents")
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_chunk_doc_index"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    document_id: Mapped[int] = mapped_column(
        ForeignKey("uploaded_documents.id", ondelete="CASCADE"), index=True
    )

    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )

    chunk_index: Mapped[int] = mapped_column(Integer)

    text: Mapped[str] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    document: Mapped["UploadedDocument"] = relationship(back_populates="chunks")
    course: Mapped["Course"] = relationship(back_populates="chunks")


class GeneratedOutput(Base):
    """An AI-generated artifact for a course, such as a summary.

    We store WHAT was generated (content), WHAT KIND it is (output_type),
    and WHICH COURSE it belongs to. It lives under a course, so when the
    course is deleted, its generated outputs are deleted with it.
    """

    __tablename__ = "generated_outputs"

    #The auto-numbered identity of this row. Every table gets one.
    id: Mapped[int] = mapped_column(primary_key=True)

    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )

    # What kind of output this is, stored as a short text label,
    # for example: "summary". Later kinds can be added without
    # changing the table structure.
    output_type: Mapped[str] = mapped_column(String(30))

    content: Mapped[str] = mapped_column(Text) #Unlimited length

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Navigation attribute (NOT a column): lets Python code write
    # output.course to reach the Course object. The partner attribute
    # on Course must be named exactly "generated_outputs".
    course: Mapped["Course"] = relationship(back_populates = "generated_outputs")

class Quiz(Base):
    """A quiz that belongs to one course."""

    __tablename__ = "quizzes"

    id: Mapped[int] = mapped_column(primary_key=True)

    #Owner course. Same cascade logic.

    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )

    title: Mapped[str] = mapped_column(String(200))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    #Navigation up to the course
    course: Mapped["Course"] = relationship(back_populates="quizzes")

    questions: Mapped[list["QuizQuestion"]] = relationship(
        back_populates="quiz", cascade= "all, delete-orphan", passive_deletes=True
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

    #The position of this question inside its quiz: 0,1,2,3...
    question_index: Mapped[int] = mapped_column(Integer)

    #The question text itself
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

    # Two automatic timestamps working together:
    #   server_default=func.now()  -> set once, when the row is born
    #   onupdate=func.now()        -> refreshed by the database every
    #                                 time the row is changed
    # So updated_at always answers "when did this student last study
    # this course?" without any application code remembering to set it.
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