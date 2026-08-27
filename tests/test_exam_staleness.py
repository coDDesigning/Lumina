"""Fingerprint comparison: what counts as a change, and what does not."""

import hashlib
from datetime import date

import pytest

from schemas.exam_mode import ExamPlanFingerprint
from services.exam_ranking import RANKING_POLICY_VERSION
from services.exam_staleness import (
    RESCAN_REASONS,
    compare_fingerprints,
    requires_rescan,
    syllabus_digest,
)
from services.exam_topics import TOPIC_KEY_VERSION


def fingerprint(**overrides) -> ExamPlanFingerprint:
    values = {
        "mastery_user_id": 7,
        "analysis_output_id": 3,
        "exam_date": None,
        "syllabus_digest": "aaa",
        "course_topic_keys": ["graph-traversal"],
        "ready_document_ids": ["doc-1"],
        "past_exam_document_ids": ["doc-2"],
        "document_revision_digest": "rev-1",
        "graded_answer_count": 4,
        "mastery_digest": "mastery-1",
        "selected_topic_keys": ["graph-traversal"],
        "high_priority_topic_keys": [],
        "ranking_policy_version": RANKING_POLICY_VERSION,
        "topic_key_version": TOPIC_KEY_VERSION,
    }
    values.update(overrides)
    return ExamPlanFingerprint(**values)


def stored(**overrides) -> dict:
    return fingerprint(**overrides).model_dump(mode="json")


def test_an_unchanged_course_reports_no_reason_at_all() -> None:
    assert compare_fingerprints(stored(), fingerprint()) == ()


def test_no_stored_fingerprint_reports_nothing_rather_than_everything() -> None:
    assert compare_fingerprints(None, fingerprint()) == ()
    assert compare_fingerprints({}, fingerprint()) == ()


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("syllabus_digest", "bbb", "syllabus_changed"),
        ("course_topic_keys", ["sorting"], "course_topics_changed"),
        ("past_exam_document_ids", ["doc-9"], "past_exams_changed"),
        ("document_revision_digest", "rev-2", "documents_reprocessed"),
        ("graded_answer_count", 9, "new_quiz_results"),
        ("selected_topic_keys", ["sorting"], "selection_changed"),
        ("high_priority_topic_keys", ["graph-traversal"], "selection_changed"),
        ("ranking_policy_version", 99, "ranking_policy_updated"),
        ("topic_key_version", 99, "topic_keys_updated"),
    ],
)
def test_each_moved_input_reports_its_own_reason(field, value, reason) -> None:
    reasons = compare_fingerprints(stored(), fingerprint(**{field: value}))

    assert reason in reasons


def test_adding_and_removing_documents_are_told_apart() -> None:
    added = compare_fingerprints(
        stored(), fingerprint(ready_document_ids=["doc-1", "doc-3"])
    )
    removed = compare_fingerprints(stored(), fingerprint(ready_document_ids=[]))
    both = compare_fingerprints(stored(), fingerprint(ready_document_ids=["doc-3"]))

    assert "documents_added" in added
    assert "documents_removed" in removed
    assert "documents_changed" in both


def test_a_new_attempt_reads_as_one_reason_rather_than_two() -> None:
    reasons = compare_fingerprints(
        stored(), fingerprint(graded_answer_count=5, mastery_digest="mastery-2")
    )

    assert "new_quiz_results" in reasons
    assert "mastery_changed" not in reasons


def test_a_regrade_that_moves_no_count_is_reported_on_its_own() -> None:
    reasons = compare_fingerprints(stored(), fingerprint(mastery_digest="mastery-2"))

    assert reasons == ("mastery_changed",)


def test_reasons_come_back_sorted_so_two_readers_agree() -> None:
    reasons = compare_fingerprints(
        stored(),
        fingerprint(
            syllabus_digest="bbb",
            course_topic_keys=["sorting"],
            ranking_policy_version=99,
        ),
    )

    assert list(reasons) == sorted(reasons)


def test_a_field_the_stored_document_lacks_is_skipped_rather_than_flagged() -> None:
    """An older plan loses one check instead of reporting a phantom change."""
    legacy = stored()
    del legacy["document_revision_digest"]
    del legacy["mastery_digest"]

    reasons = compare_fingerprints(
        legacy, fingerprint(document_revision_digest="rev-2", mastery_digest="x")
    )

    assert reasons == ()


def test_a_moved_exam_date_is_stale_without_demanding_another_scan() -> None:
    reasons = compare_fingerprints(stored(), fingerprint(exam_date=date(2030, 1, 1)))

    assert reasons == ("exam_date_changed",)
    assert requires_rescan(reasons) is False
    assert "exam_date_changed" not in RESCAN_REASONS


@pytest.mark.parametrize("reason", sorted(RESCAN_REASONS))
def test_every_ranking_input_reason_asks_for_a_rescan(reason: str) -> None:
    assert requires_rescan([reason]) is True


def test_reflowing_the_syllabus_is_not_a_change_to_the_course() -> None:
    assert syllabus_digest("Week 1  Graphs\nWeek 2 Sorting") == syllabus_digest(
        "Week 1 Graphs Week 2 Sorting"
    )


def test_an_absent_syllabus_has_no_digest_rather_than_a_digest_of_nothing() -> None:
    assert syllabus_digest(None) is None
    assert syllabus_digest("   ") is None


def test_the_digest_is_an_unsalted_hash_of_the_normalized_text() -> None:
    """Python's builtin hash is salted per process and would restart-stale everything."""
    expected = hashlib.sha256("Week 1 Graphs".encode("utf-8")).hexdigest()

    assert syllabus_digest("Week 1  Graphs ") == expected
