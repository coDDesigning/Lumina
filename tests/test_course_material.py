from datetime import datetime, timedelta, timezone

import pytest

from backend.app.models import DocumentChunk, UploadedDocument
from services.course_material import CHUNK_SEPARATOR, load_course_material

BASE_TIME = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

pytestmark = pytest.mark.database_contract


def _document(model_graph, *, file_hash: str, minutes: int, status: str = "ready"):
    return UploadedDocument(
        original_file_name=f"{file_hash[:6]}.txt",
        file_type="txt",
        mime_type="text/plain",
        file_size=10,
        file_hash=file_hash,
        uploader=model_graph.user,
        course=model_graph.course,
        storage_provider="local:test",
        storage_key=f"{file_hash[:6]}.txt",
        status=status,
        created_at=BASE_TIME + timedelta(minutes=minutes),
    )


def _chunks(db_session, model_graph, document, indexed_texts) -> None:
    for index, text in indexed_texts:
        db_session.add(
            DocumentChunk(
                document=document,
                course=model_graph.course,
                chunk_index=index,
                page_number=None,
                text=text,
            )
        )


def _single_document_material(db_session, model_graph, texts) -> None:
    document = _document(model_graph, file_hash="a" * 64, minutes=0)
    db_session.add(document)
    _chunks(db_session, model_graph, document, list(enumerate(texts)))
    db_session.commit()


def test_selection_is_ordered_by_document_age_then_chunk_index(
    db_session,
    model_graph,
) -> None:
    """Insertion order is deliberately scrambled; the result must not be."""
    later = _document(model_graph, file_hash="b" * 64, minutes=10)
    earlier = _document(model_graph, file_hash="a" * 64, minutes=0)

    db_session.add_all([later, earlier])
    db_session.flush()

    _chunks(
        db_session,
        model_graph,
        later,
        [(1, "later-one"), (0, "later-zero")],
    )
    _chunks(
        db_session,
        model_graph,
        earlier,
        [(2, "earlier-two"), (0, "earlier-zero"), (1, "earlier-one")],
    )
    db_session.commit()

    material = load_course_material(
        db_session,
        model_graph.course.id,
        max_characters=1000,
    )

    assert material.text.split(CHUNK_SEPARATOR) == [
        "earlier-zero",
        "earlier-one",
        "earlier-two",
        "later-zero",
        "later-one",
    ]
    assert material.truncated is False
    assert material.chunks_used == 5
    assert material.chunks_available == 5


def test_repeated_loads_of_unchanged_state_are_identical(
    db_session,
    model_graph,
) -> None:
    _single_document_material(
        db_session,
        model_graph,
        [f"chunk-{index} " + "x" * 30 for index in range(6)],
    )

    first = load_course_material(db_session, model_graph.course.id, max_characters=90)
    second = load_course_material(db_session, model_graph.course.id, max_characters=90)

    assert first == second
    assert first.truncated is True


def test_material_that_fits_exactly_is_not_truncated(
    db_session,
    model_graph,
) -> None:
    _single_document_material(db_session, model_graph, ["A" * 10, "B" * 10, "C" * 10])

    budget = 10 + len(CHUNK_SEPARATOR) + 10 + len(CHUNK_SEPARATOR) + 10

    material = load_course_material(
        db_session,
        model_graph.course.id,
        max_characters=budget,
    )

    assert len(material.text) == budget
    assert material.chunks_used == 3
    assert material.truncated is False


def test_one_character_over_the_budget_truncates_whole_chunks(
    db_session,
    model_graph,
) -> None:
    _single_document_material(db_session, model_graph, ["A" * 10, "B" * 10, "C" * 10])

    budget = 10 + len(CHUNK_SEPARATOR) + 10 + len(CHUNK_SEPARATOR) + 10 - 1

    material = load_course_material(
        db_session,
        model_graph.course.id,
        max_characters=budget,
    )

    assert len(material.text) <= budget
    assert material.text == "A" * 10 + CHUNK_SEPARATOR + "B" * 10
    assert material.chunks_used == 2
    assert material.chunks_available == 3
    assert material.truncated is True


def test_separators_are_counted_against_the_budget(
    db_session,
    model_graph,
) -> None:
    _single_document_material(db_session, model_graph, ["A" * 10, "B" * 10])

    material = load_course_material(
        db_session,
        model_graph.course.id,
        max_characters=20,
    )

    assert material.text == "A" * 10
    assert material.chunks_used == 1
    assert material.truncated is True


def test_blank_chunks_are_skipped_without_reporting_truncation(
    db_session,
    model_graph,
) -> None:
    _single_document_material(
        db_session,
        model_graph,
        ["  Real content  ", "   ", "\n\t\n", "More content"],
    )

    material = load_course_material(
        db_session,
        model_graph.course.id,
        max_characters=1000,
    )

    assert material.text == "Real content" + CHUNK_SEPARATOR + "More content"
    assert material.chunks_used == 2
    assert material.chunks_available == 4
    assert material.truncated is False


def test_chunks_of_documents_that_are_not_ready_are_excluded(
    db_session,
    model_graph,
) -> None:
    ready = _document(model_graph, file_hash="a" * 64, minutes=0)
    pending = _document(model_graph, file_hash="b" * 64, minutes=1, status="uploaded")
    failed = _document(model_graph, file_hash="c" * 64, minutes=2, status="failed")

    db_session.add_all([ready, pending, failed])
    db_session.flush()

    _chunks(db_session, model_graph, ready, [(0, "included")])
    _chunks(db_session, model_graph, pending, [(0, "still uploading")])
    _chunks(db_session, model_graph, failed, [(0, "failed processing")])
    db_session.commit()

    material = load_course_material(
        db_session,
        model_graph.course.id,
        max_characters=1000,
    )

    assert material.text == "included"
    assert material.chunks_available == 1


def test_material_from_another_course_is_excluded(
    db_session,
    model_graph,
) -> None:
    _single_document_material(db_session, model_graph, ["Primary course content"])

    material = load_course_material(
        db_session,
        model_graph.other_course.id,
        max_characters=1000,
    )

    assert material.text == ""
    assert material.is_empty is True
    assert material.chunks_used == 0
    assert material.chunks_available == 0
    assert material.truncated is False


def test_a_chunk_larger_than_the_whole_budget_yields_no_material(
    db_session,
    model_graph,
) -> None:
    """Guarded at startup by the budget floor, so this only covers legacy rows."""
    _single_document_material(db_session, model_graph, ["z" * 500])

    material = load_course_material(
        db_session,
        model_graph.course.id,
        max_characters=100,
    )

    assert material.is_empty is True
    assert material.chunks_used == 0
    assert material.chunks_available == 1
    assert material.truncated is True


def test_a_nonpositive_budget_is_rejected(db_session, model_graph) -> None:
    with pytest.raises(ValueError):
        load_course_material(db_session, model_graph.course.id, max_characters=0)
