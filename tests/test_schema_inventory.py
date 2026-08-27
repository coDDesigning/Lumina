import re
from pathlib import Path

from backend.app.models import Base

EXPECTED_TABLE_COUNT = 25

EXPECTED_TABLE_NAMES = {
    "ai_usage_logs",
    "chunk_embeddings",
    "conversation_messages",
    "conversations",
    "course_settings",
    "course_topics",
    "courses",
    "credit_transactions",
    "document_chunks",
    "document_pages",
    "document_visuals",
    "exam_topic_candidates",
    "generated_outputs",
    "past_exam_questions",
    "processing_jobs",
    "profile_knowledge",
    "progress",
    "quiz_attempt_answers",
    "quiz_attempts",
    "quiz_questions",
    "quizzes",
    "rate_limit_buckets",
    "roles",
    "uploaded_documents",
    "users",
}


def test_schema_table_inventory_matches_expected() -> None:
    """Verifies that the relational model defines exactly the expected 23 tables."""
    actual_tables = set(Base.metadata.tables.keys())
    assert actual_tables == EXPECTED_TABLE_NAMES, (
        f"Schema tables drifted. Difference: {actual_tables ^ EXPECTED_TABLE_NAMES}"
    )
    assert len(Base.metadata.tables) == EXPECTED_TABLE_COUNT


def test_documentation_table_counts_match_schema() -> None:
    """Ensures contributor documentation (AGENTS.md and docs/database.md) stays synchronized."""
    repo_root = Path(__file__).resolve().parents[1]

    # Check AGENTS.md
    agents_md = (repo_root / "AGENTS.md").read_text(encoding="utf-8")
    agents_match = re.search(r"defines the (\d+)-table relational model", agents_md)
    assert agents_match is not None, (
        "AGENTS.md must document the relational model table count"
    )
    agents_count = int(agents_match.group(1))
    assert agents_count == EXPECTED_TABLE_COUNT, (
        f"AGENTS.md reports {agents_count} tables, expected {EXPECTED_TABLE_COUNT}"
    )

    # Check docs/database.md
    database_md = (repo_root / "docs" / "database.md").read_text(encoding="utf-8")
    db_match = re.search(r"across all (\d+) tables", database_md)
    assert db_match is not None, "docs/database.md must document the total table count"
    db_count = int(db_match.group(1))
    assert db_count == EXPECTED_TABLE_COUNT, (
        f"docs/database.md reports {db_count} tables, expected {EXPECTED_TABLE_COUNT}"
    )
