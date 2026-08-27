"""exam mode foundation: topic candidates and past exam questions

Revision ID: a6d3f81c9b47
Revises: 4399b6d253bf
Create Date: 2026-08-27 15:20:00.000000

Exam Mode needs two things a versioned JSON document cannot give it: a
course-scoped query over the topics one analysis discovered, and a filterable
list of the questions contained in the course's past papers. Both become real
tables.

The exam plan itself stays a versioned JSON document in ``generated_outputs``
with ``output_type='exam_plan'``, because a plan is read whole, reopened whole,
and never queried by its parts. The analysis writes a ``generated_outputs`` row
of its own, ``output_type='exam_topic_analysis'``, carrying attribution and a
summary; ``exam_topic_candidates`` hangs off that row and is append-only, so an
older plan can always be reopened against the evidence it was actually built
from.

``past_exam_questions`` belongs to the paper instead. A question is a property
of the document it was printed in, not of whichever analysis happened to read
that document, so it is extracted once when the paper is uploaded and every
later analysis reads the same rows. Owning them by analysis would re-extract an
unchanged paper on every rescan, and would let two analyses of one paper
disagree about what it asks. ``document_id`` is therefore ``NOT NULL`` and
``(document_id, position)`` is the unique key: a question nobody can attribute
to a paper is not a question this system records.

Both tables denormalize ``course_id`` for course-scoped reads and hold it true
with a composite foreign key, the arrangement ``document_chunks`` and
``chunk_embeddings`` already use. For ``exam_topic_candidates`` that requires
``(id, course_id)`` on ``generated_outputs`` to be referenceable, which this
revision adds as a unique index rather than a unique constraint: SQLite has no
``ALTER TABLE ... ADD CONSTRAINT``, and rebuilding a table holding every
generation a deployment has ever produced is not a price worth paying for a
spelling. A unique index is what SQLite requires of a composite parent key and
what PostgreSQL accepts as one. Alembic cannot issue ``CONCURRENTLY`` inside a
transaction, but the one-shot migrator runs against a separate direct database
URL precisely so schema locks never traverse the runtime pool, so a plain unique
index is correct here.

Deleting a past exam therefore retracts the questions extracted from it while
the candidate aggregates keep their counts. That is deliberate: keeping verbatim
exam text alive after the student deleted the source is a retention claim this
system does not make, and the disagreement is reported rather than hidden,
because the exam plan's staleness fingerprint records which past exams it used.

The two columns added to ``uploaded_documents`` record how extraction went
without letting it fail an upload. They carry no ``CHECK``: constraining a
column on an existing table would force a ``batch_alter_table`` rebuild on
SQLite, and ``generated_outputs.output_type`` is the standing precedent for a
discriminator the application owns. ``exam_extraction_status`` defaults to
``not_applicable``, which is the truth for every document that is not an
examination paper and for every row that predates this revision.

This revision is add-only. It creates two tables and five indexes, adds one
index and two columns to existing tables, and needs no ``batch_alter_table``:
``CREATE TABLE``, ``CREATE INDEX``, ``CREATE UNIQUE INDEX``, and ``ADD COLUMN``
with a constant default are native on both supported engines. The only dialect
branch is the timezone flag on the timestamp columns, which every table in this
schema already carries. There is no data to move, so there are no ``sa.table()``
shims, and the downgrade is a real inverse that leaves the schema identical to
``f3c8d05a2b16``.
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a6d3f81c9b47"
down_revision: str | Sequence[str] | None = "4399b6d253bf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

_ASCII_WHITESPACE = " \t\n\r\v\f"

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
EXAM_EXTRACTION_NOT_APPLICABLE = "not_applicable"
_EXAM_QUESTION_DIFFICULTIES_SQL = ", ".join(
    f"'{level}'" for level in EXAM_QUESTION_DIFFICULTIES
)


def upgrade() -> None:
    postgresql = op.get_bind().dialect.name == "postgresql"

    op.create_index(
        op.f("uq_generated_outputs_id_course_id"),
        "generated_outputs",
        ["id", "course_id"],
        unique=True,
    )

    op.create_table(
        "exam_topic_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("analysis_output_id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("topic_key", sa.String(length=120), nullable=False),
        sa.Column("display_label", sa.String(length=200), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=True),
        sa.Column(
            "in_syllabus", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column(
            "in_course_topics", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column(
            "in_past_exams", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column(
            "in_material", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column(
            "discovery_confidence",
            sa.Float(),
            server_default="0.5",
            nullable=False,
        ),
        sa.Column("syllabus_weight_percent", sa.Float(), nullable=True),
        sa.Column(
            "syllabus_mention_count", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "material_chunk_count", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "material_character_count", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "past_exam_question_count", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("past_exam_marks_total", sa.Float(), nullable=True),
        sa.Column("past_exam_years", sa.JSON(), nullable=True),
        sa.Column("citations", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=postgresql),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_exam_topic_candidates")),
        sa.UniqueConstraint(
            "analysis_output_id",
            "topic_key",
            name=op.f("uq_exam_topic_candidates_analysis_output_id_topic_key"),
        ),
        sa.CheckConstraint(
            "position >= 0",
            name=op.f("ck_exam_topic_candidates_position_nonnegative"),
        ),
        sa.CheckConstraint(
            f"length(trim(topic_key, '{_ASCII_WHITESPACE}')) > 0",
            name=op.f("ck_exam_topic_candidates_topic_key_nonblank"),
        ),
        sa.CheckConstraint(
            f"length(trim(display_label, '{_ASCII_WHITESPACE}')) > 0",
            name=op.f("ck_exam_topic_candidates_display_label_nonblank"),
        ),
        sa.CheckConstraint(
            "discovery_confidence >= 0 AND discovery_confidence <= 1",
            name=op.f("ck_exam_topic_candidates_discovery_confidence_fraction"),
        ),
        sa.CheckConstraint(
            "syllabus_weight_percent IS NULL OR "
            "(syllabus_weight_percent >= 0 AND syllabus_weight_percent <= 100)",
            name=op.f("ck_exam_topic_candidates_syllabus_weight_percent_range"),
        ),
        sa.CheckConstraint(
            "syllabus_mention_count >= 0 AND material_chunk_count >= 0 AND "
            "material_character_count >= 0 AND past_exam_question_count >= 0",
            name=op.f("ck_exam_topic_candidates_evidence_counts_nonnegative"),
        ),
        sa.CheckConstraint(
            "past_exam_marks_total IS NULL OR past_exam_marks_total >= 0",
            name=op.f("ck_exam_topic_candidates_past_exam_marks_total_nonnegative"),
        ),
        sa.CheckConstraint(
            "in_syllabus OR in_course_topics OR in_past_exams OR in_material",
            name=op.f("ck_exam_topic_candidates_at_least_one_source"),
        ),
        sa.ForeignKeyConstraint(
            ["analysis_output_id", "course_id"],
            ["generated_outputs.id", "generated_outputs.course_id"],
            name=op.f("fk_exam_topic_candidates_analysis_course_generated_outputs"),
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        op.f("ix_exam_topic_candidates_analysis_output_id"),
        "exam_topic_candidates",
        ["analysis_output_id"],
    )
    op.create_index(
        op.f("ix_exam_topic_candidates_course_id"),
        "exam_topic_candidates",
        ["course_id"],
    )

    op.create_table(
        "past_exam_questions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("question_label", sa.String(length=50), nullable=True),
        sa.Column("question_number", sa.Integer(), nullable=True),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("subparts", sa.JSON(), nullable=True),
        sa.Column(
            "question_type",
            sa.String(length=30),
            server_default="unspecified",
            nullable=False,
        ),
        sa.Column("difficulty", sa.String(length=10), nullable=True),
        sa.Column("marks", sa.Float(), nullable=True),
        sa.Column("answer_guidance", sa.Text(), nullable=True),
        sa.Column("marking_points", sa.JSON(), nullable=True),
        sa.Column("visual_refs", sa.JSON(), nullable=True),
        sa.Column("topic_key", sa.String(length=120), nullable=True),
        sa.Column("topic_mappings", sa.JSON(), nullable=True),
        sa.Column("citations", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=postgresql),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_past_exam_questions")),
        sa.UniqueConstraint(
            "document_id",
            "position",
            name=op.f("uq_past_exam_questions_document_id_position"),
        ),
        sa.CheckConstraint(
            "position >= 0", name=op.f("ck_past_exam_questions_position_nonnegative")
        ),
        sa.CheckConstraint(
            f"length(trim(question_text, '{_ASCII_WHITESPACE}')) > 0",
            name=op.f("ck_past_exam_questions_question_text_nonblank"),
        ),
        sa.CheckConstraint(
            "page_start IS NULL OR page_start >= 1",
            name=op.f("ck_past_exam_questions_page_start_positive"),
        ),
        sa.CheckConstraint(
            "(page_start IS NULL AND page_end IS NULL) OR "
            "(page_start IS NOT NULL AND page_end IS NOT NULL AND "
            "page_end >= page_start)",
            name=op.f("ck_past_exam_questions_page_range_valid"),
        ),
        sa.CheckConstraint(
            "question_number IS NULL OR question_number >= 0",
            name=op.f("ck_past_exam_questions_question_number_nonnegative"),
        ),
        sa.CheckConstraint(
            "marks IS NULL OR marks >= 0",
            name=op.f("ck_past_exam_questions_marks_nonnegative"),
        ),
        sa.CheckConstraint(
            f"question_type IN ({_EXAM_QUESTION_TYPES_SQL})",
            name=op.f("ck_past_exam_questions_question_type_valid"),
        ),
        sa.CheckConstraint(
            f"difficulty IS NULL OR difficulty IN ({_EXAM_QUESTION_DIFFICULTIES_SQL})",
            name=op.f("ck_past_exam_questions_difficulty_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "course_id"],
            ["uploaded_documents.id", "uploaded_documents.course_id"],
            name=op.f("fk_past_exam_questions_document_course_uploaded_documents"),
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        op.f("ix_past_exam_questions_course_id"),
        "past_exam_questions",
        ["course_id"],
    )
    op.create_index(
        op.f("ix_past_exam_questions_document_id"),
        "past_exam_questions",
        ["document_id"],
    )
    op.create_index(
        op.f("ix_past_exam_questions_topic_key"),
        "past_exam_questions",
        ["topic_key"],
    )

    op.add_column(
        "uploaded_documents",
        sa.Column(
            "exam_extraction_status",
            sa.String(length=20),
            server_default=EXAM_EXTRACTION_NOT_APPLICABLE,
            nullable=False,
        ),
    )
    op.add_column(
        "uploaded_documents",
        sa.Column("exam_extraction_error_code", sa.String(length=100), nullable=True),
    )

    logger.info("Exam Mode foundation tables created")


def downgrade() -> None:
    op.drop_column("uploaded_documents", "exam_extraction_error_code")
    op.drop_column("uploaded_documents", "exam_extraction_status")

    op.drop_index(
        op.f("ix_past_exam_questions_topic_key"), table_name="past_exam_questions"
    )
    op.drop_index(
        op.f("ix_past_exam_questions_document_id"), table_name="past_exam_questions"
    )
    op.drop_index(
        op.f("ix_past_exam_questions_course_id"), table_name="past_exam_questions"
    )
    op.drop_table("past_exam_questions")

    op.drop_index(
        op.f("ix_exam_topic_candidates_course_id"), table_name="exam_topic_candidates"
    )
    op.drop_index(
        op.f("ix_exam_topic_candidates_analysis_output_id"),
        table_name="exam_topic_candidates",
    )
    op.drop_table("exam_topic_candidates")

    op.drop_index(
        op.f("uq_generated_outputs_id_course_id"), table_name="generated_outputs"
    )
