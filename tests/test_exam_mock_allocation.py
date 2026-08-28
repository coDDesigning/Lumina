"""How many questions each topic gets: decided in Python, never by the model.

Telling a model that one topic is "more important" and hoping produces a paper
nobody can check. These quotas are arithmetic, so the paper can be validated
against them before a single row is written.
"""

import pytest

from services.exam_mock_allocation import (
    ALLOCATION_POLICY_VERSION,
    TopicQuota,
    allocate_topic_quota,
    default_question_mix,
    validate_question_mix,
)


def topics(*weights: int) -> tuple[tuple[str, str, int], ...]:
    return tuple(
        (f"topic-{index}", f"Topic {index}", weight)
        for index, weight in enumerate(weights, start=1)
    )


def counts(quotas: tuple[TopicQuota, ...]) -> list[int]:
    return [quota.question_count for quota in quotas]


def test_the_quotas_sum_to_exactly_the_paper_length() -> None:
    quotas = allocate_topic_quota(topics(3, 2, 1), 15)

    assert sum(counts(quotas)) == 15


def test_equal_weights_share_the_paper_equally() -> None:
    quotas = allocate_topic_quota(topics(1, 1, 1), 9)

    assert counts(quotas) == [3, 3, 3]


def test_a_heavier_topic_takes_more_of_the_paper() -> None:
    quotas = allocate_topic_quota(topics(3, 2, 1), 12)

    # One question reserved per topic, then the remaining nine split 3:2:1.
    assert counts(quotas) == [6, 4, 2]
    assert sum(counts(quotas)) == 12
    assert counts(quotas) == sorted(counts(quotas), reverse=True)


def test_one_topic_takes_the_whole_paper() -> None:
    quotas = allocate_topic_quota(topics(4), 7)

    assert counts(quotas) == [7]


def test_every_requested_topic_gets_at_least_one_question() -> None:
    """A topic the student chose and the paper never asks about wasted their study."""
    quotas = allocate_topic_quota(topics(10, 1, 1), 5)

    assert min(counts(quotas)) >= 1
    assert sum(counts(quotas)) == 5


def test_exactly_one_question_per_topic_is_the_smallest_valid_paper() -> None:
    quotas = allocate_topic_quota(topics(5, 3, 1), 3)

    assert counts(quotas) == [1, 1, 1]


def test_fewer_questions_than_topics_is_refused_rather_than_silently_dropped() -> None:
    """Omitting a low-ranked topic would be a coverage promise quietly broken."""
    with pytest.raises(ValueError, match="fewer questions than topics"):
        allocate_topic_quota(topics(3, 2, 1), 2)


def test_an_empty_topic_list_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one topic"):
        allocate_topic_quota((), 5)


def test_equal_fractional_remainders_break_ties_by_plan_rank() -> None:
    """Two topics with an identical claim: the higher-ranked one wins, always."""
    quotas = allocate_topic_quota(topics(1, 1), 5)

    assert counts(quotas) == [3, 2]


def test_the_allocation_is_identical_when_called_twice() -> None:
    supplied = topics(7, 5, 5, 3, 2)

    assert allocate_topic_quota(supplied, 17) == allocate_topic_quota(supplied, 17)


def test_the_quota_carries_the_label_the_prompt_will_use() -> None:
    quotas = allocate_topic_quota(topics(2, 1), 4)

    assert [quota.topic_key for quota in quotas] == ["topic-1", "topic-2"]
    assert [quota.display_label for quota in quotas] == ["Topic 1", "Topic 2"]
    assert ALLOCATION_POLICY_VERSION >= 1


# --------------------------------------------------------------- question mix


def test_a_default_mix_sums_to_the_paper_length() -> None:
    mix = default_question_mix(15)

    assert sum(entry.count for entry in mix) == 15
    assert all(entry.count > 0 for entry in mix)


def test_the_default_mix_is_identical_when_called_twice() -> None:
    assert default_question_mix(13) == default_question_mix(13)


def test_a_mix_that_does_not_sum_to_the_paper_length_is_refused() -> None:
    with pytest.raises(ValueError, match="must sum"):
        validate_question_mix(
            [("multiple_choice", 3), ("short_answer", 3)], question_count=10
        )


def test_a_repeated_question_type_is_refused() -> None:
    with pytest.raises(ValueError, match="more than once"):
        validate_question_mix(
            [("multiple_choice", 3), ("multiple_choice", 2)], question_count=5
        )


def test_a_non_positive_count_is_refused() -> None:
    with pytest.raises(ValueError, match="positive"):
        validate_question_mix(
            [("multiple_choice", 5), ("short_answer", 0)], question_count=5
        )


def test_an_unsupported_question_type_is_refused() -> None:
    with pytest.raises(ValueError, match="not a storable"):
        validate_question_mix([("essay", 5)], question_count=5)


def test_all_four_storable_types_are_accepted() -> None:
    mix = validate_question_mix(
        [
            ("multiple_choice", 8),
            ("true_false", 2),
            ("short_answer", 3),
            ("open_ended", 2),
        ],
        question_count=15,
    )

    assert sum(entry.count for entry in mix) == 15
    assert len(mix) == 4
