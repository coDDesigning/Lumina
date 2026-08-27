"""Spreading a ranked plan across the days that are left. No model, no charge.

The plan already decided what matters and said why. Turning that order into a
schedule is arithmetic, so it is arithmetic: this module imports no database, no
provider, and no clock, and a test asserts it. The same plan yields the same
roadmap every time, which is the only way a student can be told why Day 1 looks
the way it does.

Days are labelled ``Day 1``, ``Day 2`` — never calendar dates. A student who
starts two days late still has a Day 1, a student who studies twice on Saturday
does not lose Sunday, and a roadmap stays readable after the exam it was built
for has been sat. A date would make all three of those wrong.

The last day is review, always. A schedule that fills every day with new topics
is a schedule with no time to have forgotten anything in.
"""

from dataclasses import dataclass

ROADMAP_VERSION = 1

MIN_DAYS = 1
MAX_DAYS = 30
DEFAULT_DAYS = 7

REVIEW_DAY_TITLE = "Review everything"
REVIEW_DAY_FOCUS = (
    "Reread each topic's summary and redo the questions you got wrong. Nothing new."
)

STUDY_DAY_FOCUS = "Work through each topic's guide, then its practice questions."
CATCH_UP_FOCUS = "Finish anything you did not get to, then move on."

DAY_LABEL_PREFIX = "Day"


@dataclass(frozen=True)
class RoadmapTopic:
    """One topic on one day, carrying enough to render it without a lookup."""

    topic_key: str
    display_label: str
    rank: int
    priority_band: str
    is_high_priority: bool


@dataclass(frozen=True)
class RoadmapDay:
    day: int
    label: str
    title: str
    focus: str
    is_review: bool
    topics: tuple[RoadmapTopic, ...]


@dataclass(frozen=True)
class Roadmap:
    version: int
    day_count: int
    topic_count: int
    days: tuple[RoadmapDay, ...]
    unscheduled_topics: tuple[RoadmapTopic, ...]


def resolve_day_count(days_until_exam: int | None, requested: int | None) -> int:
    """How many days the roadmap should span.

    An explicit request wins, because a student who knows they have four
    evenings knows better than the calendar. Otherwise the days remaining are
    used, bounded: a roadmap over ninety days is a calendar, not a plan, and one
    over zero days is still one day of work.
    """
    if requested is not None:
        return max(MIN_DAYS, min(MAX_DAYS, requested))
    if days_until_exam is None:
        return DEFAULT_DAYS
    return max(MIN_DAYS, min(MAX_DAYS, days_until_exam))


def build_roadmap(topics: list[RoadmapTopic], *, day_count: int) -> Roadmap:
    """Distribute ranked topics across days, front-loaded, review last.

    Topics arrive in the plan's own rank order and stay in it, so the first day
    holds the topics the plan ranked highest. Earlier days carry more, because a
    student's attention is worth more a week out than the night before, and
    because anything that slips has somewhere to slip to.

    The final day is review rather than new work whenever there is more than one
    day. With a single day there is nothing to review yet, so that day is study.
    """
    day_count = max(MIN_DAYS, min(MAX_DAYS, day_count))
    ordered = sorted(topics, key=lambda topic: (topic.rank, topic.topic_key))

    if not ordered:
        return Roadmap(
            version=ROADMAP_VERSION,
            day_count=day_count,
            topic_count=0,
            days=tuple(
                _day(index + 1, (), is_review=index == day_count - 1 and day_count > 1)
                for index in range(day_count)
            ),
            unscheduled_topics=(),
        )

    study_days = day_count - 1 if day_count > 1 else 1
    buckets = _front_loaded_split(len(ordered), study_days)

    days: list[RoadmapDay] = []
    position = 0
    for index, size in enumerate(buckets):
        assigned = tuple(ordered[position : position + size])
        position += size
        days.append(_day(index + 1, assigned, is_review=False))

    if day_count > 1:
        days.append(_day(day_count, (), is_review=True))

    return Roadmap(
        version=ROADMAP_VERSION,
        day_count=day_count,
        topic_count=len(ordered),
        days=tuple(days),
        unscheduled_topics=tuple(ordered[position:]),
    )


def _day(
    number: int, topics: tuple[RoadmapTopic, ...], *, is_review: bool
) -> RoadmapDay:
    if is_review:
        title = REVIEW_DAY_TITLE
        focus = REVIEW_DAY_FOCUS
    elif topics:
        title = ", ".join(topic.display_label for topic in topics)
        focus = STUDY_DAY_FOCUS
    else:
        title = "Catch up"
        focus = CATCH_UP_FOCUS
    return RoadmapDay(
        day=number,
        label=f"{DAY_LABEL_PREFIX} {number}",
        title=title,
        focus=focus,
        is_review=is_review,
        topics=topics,
    )


def _front_loaded_split(total: int, buckets: int) -> list[int]:
    """Split ``total`` topics across ``buckets`` days, heavier at the front.

    An even split with the remainder distributed from the first day onwards.
    Deterministic and total: every topic lands on exactly one day, and no day is
    given a fraction of one.

    When there are more days than topics the tail days come out empty, which is
    correct rather than a gap to fill: a student with twelve days and three
    topics has spare days, and inventing work for them would say the plan needs
    more time than it does.
    """
    if buckets <= 0:
        return []
    base, remainder = divmod(total, buckets)
    return [base + (1 if index < remainder else 0) for index in range(buckets)]
