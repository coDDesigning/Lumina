import pytest
from sqlalchemy.orm import Session

from backend.app.models import UploadedDocument
from services.course_status import (
    NO_DOCUMENTS,
    DocumentSignals,
    derive_status,
    document_signals,
)
from tests.conftest import ModelGraph


def _document(graph: ModelGraph, *, status: str, marker: str) -> UploadedDocument:
    return UploadedDocument(
        original_file_name=f"{marker}.txt",
        file_type="txt",
        mime_type="text/plain",
        file_size=10,
        file_hash=marker * 64,
        uploader=graph.user,
        course=graph.course,
        storage_provider="local:test",
        storage_key=f"{marker}.txt",
        status=status,
    )


@pytest.mark.parametrize(
    ("signals", "attempts_count", "average_score", "expected"),
    [
        (NO_DOCUMENTS, 0, None, "no_documents"),
        (DocumentSignals(ready_count=1), 0, None, "ready"),
        (DocumentSignals(processing_count=1), 0, None, "processing"),
        (DocumentSignals(ready_count=3, processing_count=1), 0, None, "processing"),
        (DocumentSignals(ready_count=1), 1, 0.0, "practiced"),
        (DocumentSignals(ready_count=1), 1, 0.79, "practiced"),
        (DocumentSignals(ready_count=1), 1, 0.794, "practiced"),
        (DocumentSignals(ready_count=1), 1, 0.795, "mastered"),
        (DocumentSignals(ready_count=1), 3, 0.7966666666666667, "mastered"),
        (DocumentSignals(ready_count=1), 1, 0.8, "mastered"),
        (DocumentSignals(ready_count=1), 1, 0.81, "mastered"),
        (DocumentSignals(ready_count=1), 1, 1.0, "mastered"),
    ],
)
def test_status_is_derived_from_documents_and_attempts(
    signals: DocumentSignals,
    attempts_count: int,
    average_score: float | None,
    expected: str,
) -> None:
    assert (
        derive_status(
            signals=signals,
            attempts_count=attempts_count,
            average_score=average_score,
        )
        == expected
    )


def test_a_new_upload_never_demotes_a_mastered_course() -> None:
    assert (
        derive_status(
            signals=DocumentSignals(ready_count=2, processing_count=1),
            attempts_count=4,
            average_score=0.9,
        )
        == "mastered"
    )


def test_a_new_upload_never_demotes_a_practiced_course() -> None:
    assert (
        derive_status(
            signals=DocumentSignals(ready_count=2, processing_count=1),
            attempts_count=4,
            average_score=0.5,
        )
        == "practiced"
    )


def test_an_attempt_without_a_recorded_average_still_counts_as_practice() -> None:
    assert (
        derive_status(signals=NO_DOCUMENTS, attempts_count=1, average_score=None)
        == "practiced"
    )


def test_a_course_whose_documents_all_failed_reports_no_documents(
    db_session: Session, model_graph: ModelGraph
) -> None:
    db_session.add(_document(model_graph, status="failed", marker="a"))
    db_session.flush()

    signals = document_signals(db_session, [model_graph.course.id])

    assert model_graph.course.id not in signals
    assert (
        derive_status(signals=NO_DOCUMENTS, attempts_count=0, average_score=None)
        == "no_documents"
    )


def test_a_document_being_deleted_is_not_material(
    db_session: Session, model_graph: ModelGraph
) -> None:
    db_session.add(_document(model_graph, status="deleting", marker="b"))
    db_session.flush()

    assert document_signals(db_session, [model_graph.course.id]) == {}


def test_an_uploaded_document_counts_as_processing(
    db_session: Session, model_graph: ModelGraph
) -> None:
    db_session.add(_document(model_graph, status="uploaded", marker="c"))
    db_session.add(_document(model_graph, status="processing", marker="d"))
    db_session.add(_document(model_graph, status="ready", marker="e"))
    db_session.add(_document(model_graph, status="failed", marker="f"))
    db_session.flush()

    signals = document_signals(db_session, [model_graph.course.id])

    assert signals[model_graph.course.id] == DocumentSignals(
        ready_count=1, processing_count=2
    )


def test_signals_are_scoped_to_the_requested_courses(
    db_session: Session, model_graph: ModelGraph
) -> None:
    db_session.add(_document(model_graph, status="ready", marker="1"))
    db_session.flush()

    assert document_signals(db_session, [model_graph.other_course.id]) == {}
    assert document_signals(db_session, []) == {}


def test_the_threshold_reads_the_score_the_client_displays() -> None:
    average = (0.8 + 0.8 + 0.79) / 3

    assert round(average * 100) == 80
    assert (
        derive_status(
            signals=DocumentSignals(ready_count=1),
            attempts_count=3,
            average_score=average,
        )
        == "mastered"
    )
