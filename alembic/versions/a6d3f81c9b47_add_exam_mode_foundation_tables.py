"""exam mode foundation: topic candidates and past exam questions

Revision ID: a6d3f81c9b47
Revises: f3c8d05a2b16
Create Date: 2026-08-27 15:20:00.000000

Exam Mode needs two things a versioned JSON document cannot give it: a
course-scoped query over the topics one analysis discovered, and a filterable
list of the questions it extracted from past papers. Both become real tables.

The exam plan itself stays a versioned JSON document in ``generated_outputs``
with ``output_type='exam_plan'``, because a plan is read whole, reopened whole,
and never queried by its parts. The analysis writes a ``generated_outputs`` row
of its own, ``output_type='exam_topic_analysis'``, carrying attribution and a
summary; the two new tables hang off that row and are append-only, so an older
plan can always be reopened against the evidence it was actually built from.

Both tables denormalize ``course_id`` for course-scoped reads and hold it true
with a composite foreign key, the arrangement ``document_chunks`` and
``chunk_embeddings`` already use. That requires ``(id, course_id)`` on
``generated_outputs`` to be referenceable, which this revision adds as a unique
index rather than a unique constraint: SQLite has no ``ALTER TABLE ... ADD
CONSTRAINT``, and rebuilding a table holding every generation a deployment has
ever produced is not a price worth paying for a spelling. A unique index is what
SQLite requires of a composite parent key and what PostgreSQL accepts as one.
Alembic cannot issue ``CONCURRENTLY`` inside a transaction, but the one-shot
migrator runs against a separate direct database URL precisely so schema locks
never traverse the runtime pool, so a plain unique index is correct here.

``past_exam_questions`` carries two composite foreign keys that both include
``course_id``. Together they force the analysis and the source document into the
same course, so a question can never be attributed to another course's paper.
``document_id`` is nullable so a question the model extracted but could not cite
is still recorded; a null in either half leaves that pair unchecked, and
``page_requires_document`` stops such a row claiming a page.

Deleting a past exam therefore retracts the questions extracted from it while
the candidate aggregates keep their counts. That is deliberate: keeping verbatim
exam text alive after the student deleted the source is a retention claim this
system does not make, and the disagreement is reported rather than hidden,
because the exam plan's staleness fingerprint records which past exams it used.

This revision is add-only. It creates two tables and five indexes and adds one
index to an existing table, so it needs no ``batch_alter_table``: ``CREATE
TABLE``, ``CREATE INDEX``, and ``CREATE UNIQUE INDEX`` are native on both
supported engines. The only dialect branch is the timezone flag on the
timestamp columns, which every table in this schema already carries. There is
no data to move, so there are no ``sa.table()`` shims, and the downgrade is a
real inverse that leaves the schema identical to ``f3c8d05a2b16``.
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a6d3f81c9b47"
down_revision: str | Sequence[str] | None = "f3c8d05a2b16"
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
        sa.Column("analysis_output_id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=True),
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
            "analysis_output_id",
            "position",
            name=op.f("uq_past_exam_questions_analysis_output_id_position"),
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
            "document_id IS NOT NULL OR (page_start IS NULL AND page_end IS NULL)",
            name=op.f("ck_past_exam_questions_page_requires_document"),
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
            ["analysis_output_id", "course_id"],
            ["generated_outputs.id", "generated_outputs.course_id"],
            name=op.f("fk_past_exam_questions_analysis_course_generated_outputs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "course_id"],
            ["uploaded_documents.id", "uploaded_documents.course_id"],
            name=op.f("fk_past_exam_questions_document_course_uploaded_documents"),
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        op.f("ix_past_exam_questions_analysis_output_id"),
        "past_exam_questions",
        ["analysis_output_id"],
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

    logger.info("Exam Mode foundation tables created")


def downgrade() -> None:
    op.drop_index(
        op.f("ix_past_exam_questions_topic_key"), table_name="past_exam_questions"
    )
    op.drop_index(
        op.f("ix_past_exam_questions_document_id"), table_name="past_exam_questions"
    )
    op.drop_index(
        op.f("ix_past_exam_questions_course_id"), table_name="past_exam_questions"
    )
    op.drop_index(
        op.f("ix_past_exam_questions_analysis_output_id"),
        table_name="past_exam_questions",
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
