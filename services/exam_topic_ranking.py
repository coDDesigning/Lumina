"""Ranking the topics an exam plan schedules.

This is the ranked-topic plan a roadmap consumes, and it is deliberately pure:
it takes the course's declared topics and the mastery the quiz history already
computed, and returns an order. No database, no settings, no provider, so the
ordering rules can be argued with in a test rather than inferred from a schedule.

Two signals decide the order.

``importance`` comes from whether the course declares the topic. A topic on the
syllabus is in scope by the owner's own statement; a topic that exists only
because a quiz question was tagged with it is evidence of study but not a
declaration of scope, so it weighs less. Syllabus *position* is deliberately not
read as importance: the course data records the order topics are taught in, not
how heavily they are examined, and turning a sequence into a weighting would
invent a fact the student never supplied. Position is used for sequencing, which
is what it actually means.

``weakness`` comes from mastery. A topic that has never been quizzed has no
mastery, which is not the same as a mastery of zero: an unquizzed topic is
ranked with a neutral weakness so a course with no attempt at all falls back to
importance and syllabus order instead of pretending every topic is failing.

The weights are equal, so neither signal can silence the other: of two topics
the course declares, the weaker one ranks first, and of two topics equally weak,
the declared one ranks first. A declared topic the student has already mastered
can still fall behind an undeclared topic they are failing, which is the trade
this ranking intends -- it decides the order of attack, not what gets dropped.
Nothing is hidden by it either, because the schedule gives every selected topic
a first pass before any topic gets a second one.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from schemas.exam_roadmap import TopicSource

SYLLABUS_IMPORTANCE = 1.0
UNLISTED_IMPORTANCE = 0.6

# An unquizzed topic is neither known-weak nor known-strong.
UNKNOWN_WEAKNESS = 0.5

IMPORTANCE_WEIGHT = 0.5
WEAKNESS_WEIGHT = 0.5

# Priorities are compared, persisted, and asserted on, so they are rounded to a
# width that survives a JSON round trip unchanged.
PRIORITY_PRECISION = 4


@dataclass(frozen=True)
class RankedTopic:
    """One topic of the ranked plan, with everything the ranking used."""

    topic: str
    source: TopicSource
    syllabus_position: int | None
    importance: float
    mastery_percentage: int | None
    questions_answered: int
    weakness: float
    priority: float


def _normalize(topic: str) -> str:
    return " ".join(topic.split()).casefold()


def _weakness(mastery_percentage: int | None) -> float:
    if mastery_percentage is None:
        return UNKNOWN_WEAKNESS
    bounded = min(100, max(0, mastery_percentage))
    return round((100 - bounded) / 100, PRIORITY_PRECISION)


def _priority(importance: float, weakness: float) -> float:
    return round(
        IMPORTANCE_WEIGHT * importance + WEAKNESS_WEIGHT * weakness,
        PRIORITY_PRECISION,
    )


def _sort_key(topic: RankedTopic) -> tuple[float, int, str]:
    # Position orders ties so the earlier-taught topic comes first, and a topic
    # with no position sorts after every topic that has one.
    position = topic.syllabus_position
    return (
        -topic.priority,
        position if position is not None else 1_000_000,
        _normalize(topic.topic),
    )


def rank_topics(
    *,
    syllabus_topics: Sequence[str],
    mastery: Iterable[object],
) -> list[RankedTopic]:
    """Rank a course's topics for exam planning, highest priority first.

    ``mastery`` is any iterable of objects carrying ``topic``,
    ``mastery_percentage`` and ``questions_answered`` -- the ``TopicMastery``
    entries the progress aggregate already produces. Mastery for a topic the
    syllabus does not declare still ranks, because a student who has been
    quizzed on something is studying it.
    """
    measured = {
        _normalize(entry.topic): entry
        for entry in mastery
        if getattr(entry, "topic", "").strip()
    }

    ranked: list[RankedTopic] = []
    seen: set[str] = set()

    for position, name in enumerate(syllabus_topics):
        topic = " ".join(name.split())
        if not topic:
            continue
        key = _normalize(topic)
        if key in seen:
            continue
        seen.add(key)
        entry = measured.get(key)
        mastery_percentage = entry.mastery_percentage if entry is not None else None
        weakness = _weakness(mastery_percentage)
        ranked.append(
            RankedTopic(
                topic=topic,
                source=TopicSource.SYLLABUS,
                syllabus_position=position,
                importance=SYLLABUS_IMPORTANCE,
                mastery_percentage=mastery_percentage,
                questions_answered=(
                    entry.questions_answered if entry is not None else 0
                ),
                weakness=weakness,
                priority=_priority(SYLLABUS_IMPORTANCE, weakness),
            )
        )

    for key, entry in measured.items():
        if key in seen:
            continue
        seen.add(key)
        weakness = _weakness(entry.mastery_percentage)
        ranked.append(
            RankedTopic(
                topic=" ".join(entry.topic.split()),
                source=TopicSource.QUIZ,
                syllabus_position=None,
                importance=UNLISTED_IMPORTANCE,
                mastery_percentage=entry.mastery_percentage,
                questions_answered=entry.questions_answered,
                weakness=weakness,
                priority=_priority(UNLISTED_IMPORTANCE, weakness),
            )
        )

    ranked.sort(key=_sort_key)
    return ranked
