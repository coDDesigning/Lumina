"""Allocating ranked topics across the days a student actually has left.

Pure scheduling: dates in, days out. It reads no database, calls no provider,
and is the only place that decides what a horizon means, so every boundary the
calendar can produce is a table in a test rather than a branch discovered in
production.

The rules, in one place:

* The plan runs from today through the exam date inclusive, so an exam today is
  a one-day plan rather than an error.
* A horizon of one day or less is triage: every day carries the same short list
  of the highest-priority topics, because there is no time for a second pass to
  mean anything.
* Otherwise the exam day itself is a final review and the days before it are
  study days. Coverage comes first: every selected topic gets one pass, spread
  evenly, before any topic gets a second. That is what stops a weak topic from
  eating the whole plan and hiding a topic the syllabus declares.
* Selection is by priority, sequencing is by syllabus position. When the horizon
  cannot hold every topic, the ones it holds are the highest-priority ones, but
  they are still taught in the order the course teaches them, so a prerequisite
  is never scheduled after the topic that needs it.
* Days left over after coverage cycle the ranked order again, highest priority
  first, so the weakest and most important topics collect the most passes.
* A horizon longer than ``MAX_PLAN_DAYS`` is capped and the plan starts later;
  the days before it are reported rather than filled with invented work.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from math import ceil

from schemas.exam_roadmap import RoadmapDayKind, RoadmapHorizon
from services.exam_topic_ranking import RankedTopic

MAX_PLAN_DAYS = 90
DEFAULT_MAX_TOPICS_PER_DAY = 3
REVIEW_TOPICS_PER_DAY = 2
TRIAGE_TOPIC_LIMIT = 6
FINAL_REVIEW_TOPIC_LIMIT = 4

# At or below this many days until the exam, the plan is a triage plan.
TRIAGE_HORIZON_DAYS = 1

WEAK_MASTERY_THRESHOLD = 60
STRONG_MASTERY_THRESHOLD = 80


@dataclass(frozen=True)
class ScheduledTopic:
    topic: RankedTopic
    pass_number: int
    goal: str


@dataclass(frozen=True)
class ScheduledDay:
    date: date
    kind: RoadmapDayKind
    is_exam_day: bool
    focus: str
    topics: tuple[ScheduledTopic, ...]


@dataclass(frozen=True)
class Schedule:
    days: tuple[ScheduledDay, ...]
    deferred: tuple[RankedTopic, ...]
    horizon: RoadmapHorizon
    starts_on: date
    lead_in_days: int
    days_until_exam: int
    notes: tuple[str, ...]


def _mastery_clause(topic: RankedTopic) -> str:
    if topic.mastery_percentage is None:
        return "you have not been quizzed on it yet"
    return f"you are at {topic.mastery_percentage}% on it"


def _first_pass_goal(topic: RankedTopic) -> str:
    name = topic.topic
    mastery = topic.mastery_percentage
    if mastery is None:
        return (
            f"Foundational pass on {name}. Work through lecture concepts end to end, "
            "take structured notes, and formulate practice questions."
        )
    if mastery < WEAK_MASTERY_THRESHOLD:
        return (
            f"Rebuild {name} from the ground up -- {_mastery_clause(topic)}. Review "
            "core definitions in your material, re-examine worked examples, and take a focused quiz."
        )
    if mastery < STRONG_MASTERY_THRESHOLD:
        return (
            f"Targeted reinforcement on {name} -- {_mastery_clause(topic)}. Drill "
            "into the specific sections and question types you previously missed."
        )
    return (
        f"Maintenance & mastery retention for {name} -- {_mastery_clause(topic)}. "
        "Skim notes, solve an advanced challenge problem, and verify retention."
    )


def _review_goal(topic: RankedTopic, pass_number: int) -> str:
    if pass_number == 2:
        return (
            f"Pass 2 on {topic.topic}: active recall & synthesis. Summarize key concepts "
            "from memory first, then verify tricky sections against course materials."
        )
    if pass_number == 3:
        return (
            f"Pass 3 on {topic.topic}: problem-solving focus. Work through exam-style "
            "problems and practice questions on edge cases."
        )
    return (
        f"Pass {pass_number} on {topic.topic}: rapid reinforcement. Speed-run definitions, "
        "core formulas, and high-yield question patterns."
    )


def _triage_goal(topic: RankedTopic) -> str:
    return (
        f"High-yield triage on {topic.topic}: focus exclusively on essential definitions, "
        "core formulas, and the worked problems you find most challenging."
    )


def _goal(topic: RankedTopic, *, kind: RoadmapDayKind, pass_number: int) -> str:
    if kind in (RoadmapDayKind.LAST_MINUTE, RoadmapDayKind.FINAL_REVIEW):
        return _triage_goal(topic)
    if pass_number == 1:
        return _first_pass_goal(topic)
    return _review_goal(topic, pass_number)


def _focus(kind: RoadmapDayKind, topics: Sequence[ScheduledTopic]) -> str:
    names = ", ".join(scheduled.topic.topic for scheduled in topics)
    if kind is RoadmapDayKind.LAST_MINUTE:
        return f"Last-minute review: {names}"
    if kind is RoadmapDayKind.FINAL_REVIEW:
        return f"Exam day. Final review: {names}"
    if kind is RoadmapDayKind.REVIEW:
        return f"Reinforce: {names}"
    return f"First pass: {names}"


def _sequence_key(topic: RankedTopic) -> tuple[int, str]:
    position = topic.syllabus_position
    return (
        position if position is not None else 1_000_000,
        " ".join(topic.topic.split()).casefold(),
    )


class _Passes:
    """Counts the pass a topic is on, so a goal can name it."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def next(self, topic: RankedTopic) -> int:
        key = " ".join(topic.topic.split()).casefold()
        count = self._counts.get(key, 0) + 1
        self._counts[key] = count
        return count


def _day(
    day: date,
    kind: RoadmapDayKind,
    topics: Sequence[RankedTopic],
    passes: _Passes,
    *,
    is_exam_day: bool = False,
) -> ScheduledDay:
    scheduled: list[ScheduledTopic] = []
    for topic in topics:
        pass_number = passes.next(topic)
        scheduled.append(
            ScheduledTopic(
                topic=topic,
                pass_number=pass_number,
                goal=_goal(topic, kind=kind, pass_number=pass_number),
            )
        )
    return ScheduledDay(
        date=day,
        kind=kind,
        is_exam_day=is_exam_day,
        focus=_focus(kind, scheduled),
        topics=tuple(scheduled),
    )


def _horizon(days_until_exam: int, lead_in_days: int) -> RoadmapHorizon:
    if days_until_exam == 0:
        return RoadmapHorizon.ZERO_DAY
    if days_until_exam == 1:
        return RoadmapHorizon.ONE_DAY
    if lead_in_days:
        return RoadmapHorizon.LONG
    return RoadmapHorizon.STANDARD


def _triage_schedule(
    ranked: Sequence[RankedTopic],
    plan_dates: Sequence[date],
) -> tuple[tuple[ScheduledDay, ...], tuple[RankedTopic, ...]]:
    selected = list(ranked[:TRIAGE_TOPIC_LIMIT])
    deferred = tuple(ranked[TRIAGE_TOPIC_LIMIT:])
    passes = _Passes()
    days = tuple(
        _day(
            day,
            RoadmapDayKind.LAST_MINUTE,
            selected,
            passes,
            is_exam_day=index == len(plan_dates) - 1,
        )
        for index, day in enumerate(plan_dates)
    )
    return days, deferred


def _standard_schedule(
    ranked: Sequence[RankedTopic],
    plan_dates: Sequence[date],
    *,
    max_topics_per_day: int,
) -> tuple[tuple[ScheduledDay, ...], tuple[RankedTopic, ...]]:
    study_dates = list(plan_dates[:-1])
    exam_day = plan_dates[-1]

    capacity = len(study_dates) * max_topics_per_day
    selected = list(ranked[:capacity])
    deferred = tuple(ranked[capacity:])

    per_day = min(max_topics_per_day, max(1, ceil(len(selected) / len(study_dates))))
    coverage = sorted(selected, key=_sequence_key)

    passes = _Passes()
    days: list[ScheduledDay] = []
    for index in range(0, len(coverage), per_day):
        days.append(
            _day(
                study_dates[len(days)],
                RoadmapDayKind.STUDY,
                coverage[index : index + per_day],
                passes,
            )
        )

    cursor = 0
    for day in study_dates[len(days) :]:
        topics = [
            selected[(cursor + offset) % len(selected)]
            for offset in range(min(REVIEW_TOPICS_PER_DAY, len(selected)))
        ]
        cursor = (cursor + len(topics)) % len(selected)
        days.append(_day(day, RoadmapDayKind.REVIEW, topics, passes))

    days.append(
        _day(
            exam_day,
            RoadmapDayKind.FINAL_REVIEW,
            selected[:FINAL_REVIEW_TOPIC_LIMIT],
            passes,
            is_exam_day=True,
        )
    )
    return tuple(days), deferred


def build_schedule(
    ranked: Sequence[RankedTopic],
    *,
    today: date,
    exam_date: date,
    max_topics_per_day: int = DEFAULT_MAX_TOPICS_PER_DAY,
) -> Schedule:
    """Allocate ranked topics across every day from ``today`` to ``exam_date``.

    ``exam_date`` must not be in the past and ``ranked`` must not be empty; both
    are states the caller reports to the student rather than states this module
    guesses its way out of.
    """
    if exam_date < today:
        raise ValueError("The exam date has already passed")
    if not ranked:
        raise ValueError("An exam roadmap needs at least one ranked topic")
    if max_topics_per_day < 1:
        raise ValueError("max_topics_per_day must be a positive integer")

    days_until_exam = (exam_date - today).days
    total_days = days_until_exam + 1
    lead_in_days = max(0, total_days - MAX_PLAN_DAYS)
    starts_on = today + timedelta(days=lead_in_days)
    plan_dates = [
        starts_on + timedelta(days=offset)
        for offset in range(total_days - lead_in_days)
    ]

    if days_until_exam <= TRIAGE_HORIZON_DAYS:
        days, deferred = _triage_schedule(ranked, plan_dates)
    else:
        days, deferred = _standard_schedule(
            ranked, plan_dates, max_topics_per_day=max_topics_per_day
        )

    notes: list[str] = []
    if lead_in_days:
        notes.append(
            f"The exam is {days_until_exam} days away, so this plan covers the "
            f"final {len(plan_dates)} days and starts on {starts_on.isoformat()}."
        )
    if deferred:
        notes.append(
            f"{len(deferred)} lower-priority topics did not fit in the remaining "
            "days and are listed separately."
        )

    return Schedule(
        days=days,
        deferred=deferred,
        horizon=_horizon(days_until_exam, lead_in_days),
        starts_on=starts_on,
        lead_in_days=lead_in_days,
        days_until_exam=days_until_exam,
        notes=tuple(notes),
    )
