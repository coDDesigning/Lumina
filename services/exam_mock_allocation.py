"""How a mock exam is divided between topics and question types.

Pure arithmetic, deliberately. Telling a model that one topic matters more and
hoping for the right proportion produces a paper nobody can check: the only way
to know whether the plan's ranking reached the exam is to decide the numbers
here and then refuse a paper that does not match them.

The topic split is largest-remainder over the plan's own weights, with one
question reserved for every requested topic first. A topic a student chose and
the paper never asks about is a topic they were told to study for nothing, so a
paper too short to cover them all is refused rather than quietly narrowed.

Ties are broken by plan rank and then by topic key, so the same request always
produces the same paper. That matters because the quotas are persisted and the
provider's output is validated against them: a split that drifted between the
decision and the check would fail papers that were actually correct.

Nothing here imports a database, a provider, or a clock.
"""

from collections.abc import Sequence
from dataclasses import dataclass

ALLOCATION_POLICY_VERSION = 1

QUESTION_TYPE_MULTIPLE_CHOICE = "multiple_choice"
QUESTION_TYPE_TRUE_FALSE = "true_false"
QUESTION_TYPE_SHORT_ANSWER = "short_answer"
QUESTION_TYPE_OPEN_ENDED = "open_ended"

# The four a quiz_questions row can hold. A type outside this set is refused
# here rather than discovered by a CHECK constraint at write time.
STORABLE_QUESTION_TYPES = (
    QUESTION_TYPE_MULTIPLE_CHOICE,
    QUESTION_TYPE_TRUE_FALSE,
    QUESTION_TYPE_SHORT_ANSWER,
    QUESTION_TYPE_OPEN_ENDED,
)

# The shape of a paper nobody configured: mostly recognition, some recall, a
# little writing. Weights rather than counts, so it scales to any length.
DEFAULT_MIX_WEIGHTS = (
    (QUESTION_TYPE_MULTIPLE_CHOICE, 5),
    (QUESTION_TYPE_SHORT_ANSWER, 3),
    (QUESTION_TYPE_OPEN_ENDED, 2),
)


@dataclass(frozen=True)
class TopicQuota:
    """Exactly how many questions one topic is owed."""

    topic_key: str
    display_label: str
    weight: int
    question_count: int


@dataclass(frozen=True)
class TypeQuota:
    """Exactly how many questions one question type is owed."""

    question_type: str
    count: int


def _largest_remainder(
    weights: Sequence[int], total: int, order: Sequence[tuple[int, str]]
) -> list[int]:
    """Distribute ``total`` across ``weights``, giving leftovers to the closest.

    ``order`` is the tie-break key per position, applied when two positions have
    an identical fractional claim, so the result never depends on sort stability.
    """
    if total <= 0:
        return [0] * len(weights)

    weight_total = sum(weights)
    if weight_total <= 0:
        weights = [1] * len(weights)
        weight_total = len(weights)

    exact = [total * weight / weight_total for weight in weights]
    allocated = [int(value) for value in exact]

    leftover = total - sum(allocated)
    if leftover:
        ranked = sorted(
            range(len(weights)),
            key=lambda index: (
                -(exact[index] - allocated[index]),
                order[index],
            ),
        )
        for index in ranked[:leftover]:
            allocated[index] += 1
    return allocated


def allocate_topic_quota(
    topics: Sequence[tuple[str, str, int]], question_count: int
) -> tuple[TopicQuota, ...]:
    """Split a paper of ``question_count`` questions across ranked topics.

    ``topics`` is ``(topic_key, display_label, weight)`` in the plan's own rank
    order. Every topic is guaranteed one question before the remainder is
    shared, which is why a paper shorter than the topic list is an error rather
    than a narrower paper.
    """
    if not topics:
        raise ValueError("A mock exam needs at least one topic")
    if question_count < len(topics):
        raise ValueError(
            "A mock exam cannot cover every requested topic with fewer questions "
            "than topics"
        )

    order = [(index, topic[0]) for index, topic in enumerate(topics)]
    weights = [max(0, topic[2]) for topic in topics]
    shared = _largest_remainder(weights, question_count - len(topics), order)

    return tuple(
        TopicQuota(
            topic_key=topic_key,
            display_label=display_label,
            weight=weight,
            question_count=1 + extra,
        )
        for (topic_key, display_label, weight), extra in zip(topics, shared)
    )


def validate_question_mix(
    mix: Sequence[tuple[str, int]], *, question_count: int
) -> tuple[TypeQuota, ...]:
    """Check a requested type split, or say exactly what is wrong with it."""
    if not mix:
        raise ValueError("A question mix must name at least one question type")

    seen: set[str] = set()
    for question_type, count in mix:
        if question_type not in STORABLE_QUESTION_TYPES:
            raise ValueError(f"{question_type} is not a storable question type")
        if question_type in seen:
            raise ValueError(f"{question_type} appears more than once in the mix")
        seen.add(question_type)
        if count <= 0:
            raise ValueError(f"{question_type} must have a positive question count")

    total = sum(count for _, count in mix)
    if total != question_count:
        raise ValueError(
            f"The question mix must sum to the paper length: {total} != {question_count}"
        )

    return tuple(
        TypeQuota(question_type=question_type, count=count)
        for question_type, count in mix
    )


def default_question_mix(question_count: int) -> tuple[TypeQuota, ...]:
    """The split for a paper nobody configured, scaled to its length.

    Derived here rather than left to the provider, so an unconfigured paper is
    still a paper whose shape was decided before it was written.
    """
    if question_count <= 0:
        raise ValueError("A mock exam needs at least one question")

    types = [question_type for question_type, _ in DEFAULT_MIX_WEIGHTS]
    weights = [weight for _, weight in DEFAULT_MIX_WEIGHTS]

    if question_count < len(types):
        return (TypeQuota(question_type=types[0], count=question_count),)

    order = [(index, types[index]) for index in range(len(types))]
    shared = _largest_remainder(weights, question_count - len(types), order)
    return tuple(
        TypeQuota(question_type=question_type, count=1 + extra)
        for question_type, extra in zip(types, shared)
    )
