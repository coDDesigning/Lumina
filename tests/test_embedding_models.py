from pathlib import Path

from backend.app.embedding_models import EMBEDDING_MODEL, SCHEMA_EMBEDDING_DIMENSIONS
from backend.app.models import EMBEDDING_DIMENSIONS


def test_spec_declares_usable_widths():
    assert EMBEDDING_MODEL.dimensions > 0
    assert EMBEDDING_MODEL.max_sequence_length > 0


def test_spec_declares_both_prefixes_or_neither():
    assert bool(EMBEDDING_MODEL.query_prefix) == bool(EMBEDDING_MODEL.passage_prefix)


def test_spec_prefixes_are_distinct_when_declared():
    if EMBEDDING_MODEL.query_prefix:
        assert EMBEDDING_MODEL.query_prefix != EMBEDDING_MODEL.passage_prefix


def test_model_width_matches_the_migrated_schema():
    assert EMBEDDING_MODEL.dimensions == SCHEMA_EMBEDDING_DIMENSIONS


def test_models_reads_the_schema_width_from_the_registry():
    assert EMBEDDING_DIMENSIONS == SCHEMA_EMBEDDING_DIMENSIONS


def test_models_module_does_not_import_settings():
    source = Path("backend/app/models.py").read_text(encoding="utf-8")
    assert "from .config" not in source
    assert "backend.app.config" not in source


def test_embedding_models_module_does_not_read_the_environment():
    source = Path("backend/app/embedding_models.py").read_text(encoding="utf-8")
    assert "os.environ" not in source
    assert "getenv" not in source
