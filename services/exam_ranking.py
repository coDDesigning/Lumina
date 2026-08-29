"""The deterministic engine that orders the topics a student chose to study.

A provider may help discover what the course contains. It never decides what
matters. Everything in this module is a pure function of values already
persisted by an analysis, so the same stored evidence always produces the same
plan, byte for byte, and a test can prove it without a provider, a database, or
a clock.

Four signals carry weight: what the syllabus emphasises, how often a topic has
appeared in the selected past papers, how far the student's measured mastery
falls short, and how much course material actually covers it. A signal the
course cannot supply is not scored as zero; it is removed and its weight is
redistributed across the signals that remain, because "no past exams were
selected" and "this topic never appeared in one" are different facts and only
one of them is evidence.

The student's explicit priority mark is not a fifth signal. It is an ordering
tier applied above the computed score, so an override the student made is
visible as an override rather than hidden inside an unexplained boost.
"""

import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

from schemas.quiz_attempt import MASTERED_THRESHOLD, NEEDS_REVIEW_THRESHOLD

RANKING_POLICY_VERSION = 1

SIGNAL_SYLLABUS = "syllabus"
SIGNAL_PAST_EXAM = "past_exam"
SIGNAL_MASTERY = "mastery"
SIGNAL_MATERIAL = "material"

SIGNAL_ORDER: tuple[str, ...] = (
    SIGNAL_SYLLABUS,
    SIGNAL_PAST_EXAM,
    SIGNAL_MASTERY,
    SIGNAL_MATERIAL,
)

BASE_WEIGHTS: dict[str, int] = {
    SIGNAL_SYLLABUS: 30,
    SIGNAL_PAST_EXAM: 25,
    SIGNAL_MASTERY: 25,
    SIGNAL_MATERIAL: 20,
}

SYLLABUS_LISTED_BASE = 0.5
SYLLABUS_STUDENT_DECLARED = 0.35
UNATTEMPTED_MASTERY_GAP = 0.60
MASTERY_CONFIDENCE_QUESTIONS = 5

HIGH_PRIORITY_SCORE = 70
MEDIUM_PRIORITY_SCORE = 40
HIGH_PRIORITY_MAX_SHARE_PERCENT = 30
HIGH_PRIORITY_MIN_COUNT = 1

BAND_HIGH = "high"
BAND_MEDIUM = "medium"
BAND_LOW = "low"

BASIS_DECLARED_WEIGHT = "declared_weight"
BASIS_MENTION_COUNT = "mention_count"
BASIS_STUDENT_DECLARED = "student_declared"
BASIS_MARKS = "marks"
BASIS_QUESTION_COUNT = "question_count"
BASIS_ANSWERED = "answered"
BASIS_CHARACTERS = "characters"
BASIS_CHUNKS = "chunks"
BASIS_NEUTRAL_PRIOR = "neutral_prior"
BASIS_NO_EVIDENCE = "no_evidence"

REASON_STRONG_COMPONENT = 60
REASON_THIN_COMPONENT = 20
MAX_EXPLANATION_REASONS = 3

REASON_ORDER: tuple[str, ...] = (
    "student_marked_priority",
    "syllabus_weighted",
    "syllabus_listed",
    "student_declared_topic",
    "past_exam_frequent",
    "past_exam_present",
    "not_yet_practised",
    "mastery_weak",
    "mastery_partial",
    "mastery_strong",
    "material_heavy",
    "material_thin",
    "no_material_found",
    "insufficient_evidence",
    "syllabus_unavailable",
    "past_exam_unavailable",
    "mastery_unavailable",
    "material_unavailable",
)

REASON_SENTENCES: dict[str, str] = {
    "student_marked_priority": "You marked this topic as high priority.",
    "syllabus_weighted": "The syllabus gives it {syllabus_weight_percent:g}% of the course.",
    "syllabus_listed": "The syllabus lists it.",
    "student_declared_topic": "You listed it as a course topic.",
    "past_exam_frequent": "It appears often in the past papers analysed.",
    "past_exam_present": "It appears in {past_exam_question_count} past exam question(s).",
    "not_yet_practised": "You have not answered a quiz question on it yet.",
    "mastery_weak": "You are scoring {mastery_percentage}% on it.",
    "mastery_partial": "You are scoring {mastery_percentage}% on it.",
    "mastery_strong": "You are already scoring {mastery_percentage}% on it.",
    "material_heavy": "Your course material covers it in depth.",
    "material_thin": "Your course material barely covers it.",
    "no_material_found": "No processed material was found for it.",
    "insufficient_evidence": (
        "There was not enough evidence to prioritize it, so it is kept because you "
        "selected it."
    ),
    "syllabus_unavailable": (
        "No syllabus evidence was available, so its weighting was redistributed."
    ),
    "past_exam_unavailable": (
        "No past exam evidence was available, so its weighting was redistributed."
    ),
    "mastery_unavailable": (
        "No quiz results were available, so their weighting was redistributed."
    ),
    "material_unavailable": (
        "No processed material was available, so its weighting was redistributed."
    ),
}


@dataclass(frozen=True, slots=True)
class TopicSignals:
    """The persisted evidence one selected topic is ranked from."""

    topic_key: str
    display_label: str
    is_high_priority: bool = False
    in_syllabus: bool = False
    in_course_topics: bool = False
    syllabus_weight_percent: float | None = None
    syllabus_mention_count: int = 0
    syllabus_position: int = sys.maxsize
    past_exam_question_count: int = 0
    past_exam_marks_total: float | None = None
    material_chunk_count: int = 0
    material_character_count: int = 0
    mastery_questions_answered: int = 0
    mastery_questions_correct: int = 0


@dataclass(frozen=True, slots=True)
class SignalAvailability:
    """Which signals the selected set can supply at all.

    Availability is a property of the cohort, not of one topic, because it
    fixes the weight vector and two topics scored under different weights
    cannot be compared. Within an available signal, a topic with no evidence
    still records that fact through its own breakdown.
    """

    syllabus: bool = False
    past_exam: bool = False
    mastery: bool = False
    material: bool = False

    def get(self, signal: str) -> bool:
        return bool(getattr(self, signal))

    def as_dict(self) -> dict[str, bool]:
        return {signal: self.get(signal) for signal in SIGNAL_ORDER}


@dataclass(frozen=True, slots=True)
class SignalBreakdown:
    """One signal's contribution to one topic, as it is persisted.

    ``available`` false serialises ``raw_value`` and ``normalized_value`` as
    null rather than zero, because an absent measurement is not a measurement
    of nothing.
    """

    available: bool
    raw_value: float | None
    normalized_value: int | None
    effective_weight: int
    basis: str

    def as_dict(self) -> dict[str, object]:
        return {
            "available": self.available,
            "raw_value": self.raw_value,
            "normalized_value": self.normalized_value,
            "effective_weight": self.effective_weight,
            "basis": self.basis,
        }


@dataclass(frozen=True, slots=True)
class RankedTopic:
    """One selected topic, placed and explained."""

    topic_key: str
    display_label: str
    rank: int
    is_high_priority: bool
    priority_score: int
    priority_band: str
    has_any_evidence: bool
    is_unattempted: bool
    mastery_percentage: int | None
    signals: dict[str, SignalBreakdown]
    reason_codes: tuple[str, ...]
    explanation: str

    def as_dict(self) -> dict[str, object]:
        return {
            "topic_key": self.topic_key,
            "display_label": self.display_label,
            "rank": self.rank,
            "is_high_priority": self.is_high_priority,
            "priority_score": self.priority_score,
            "priority_band": self.priority_band,
            "has_any_evidence": self.has_any_evidence,
            "is_unattempted": self.is_unattempted,
            "mastery_percentage": self.mastery_percentage,
            "signals": {
                signal: self.signals[signal].as_dict() for signal in SIGNAL_ORDER
            },
            "reason_codes": list(self.reason_codes),
            "explanation": self.explanation,
        }


@dataclass(frozen=True, slots=True)
class RankingResult:
    """Everything one ranking run produced, ready to persist."""

    topics: tuple[RankedTopic, ...]
    effective_weights: dict[str, int]
    signals_available: dict[str, bool]
    signal_bases: dict[str, str]
    ranking_policy_version: int = RANKING_POLICY_VERSION
    configured_weights: dict[str, int] = field(
        default_factory=lambda: dict(BASE_WEIGHTS)
    )


def _round_half_up(value: float) -> int:
    """Round to the nearest integer, halves upward.

    The builtin ``round`` uses banker's rounding, so ``round(0.5)`` is 0. That
    is deterministic but wrong here: it would drop a component sitting exactly
    on a half while keeping the one above it, for no reason a reader could see.
    """
    return int(Decimal(repr(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def effective_weights(availability: SignalAvailability) -> dict[str, int]:
    """Redistribute the configured weights across the available signals.

    The result always sums to exactly 100. The remainder from truncation is
    handed out by largest fractional part, ties broken by ``SIGNAL_ORDER``, so
    the integers never depend on dictionary iteration order.
    """
    available = [signal for signal in SIGNAL_ORDER if availability.get(signal)]
    if not available:
        return {signal: 0 for signal in SIGNAL_ORDER}

    total = sum(BASE_WEIGHTS[signal] for signal in available)
    exact = {signal: BASE_WEIGHTS[signal] * 100 / total for signal in available}
    floors = {signal: int(exact[signal]) for signal in available}
    remainder = 100 - sum(floors.values())
    ranked = sorted(
        available,
        key=lambda signal: (
            -(exact[signal] - floors[signal]),
            SIGNAL_ORDER.index(signal),
        ),
    )
    for signal in ranked[:remainder]:
        floors[signal] += 1
    return {signal: floors.get(signal, 0) for signal in SIGNAL_ORDER}


def derive_availability(signals: Sequence[TopicSignals]) -> SignalAvailability:
    """Which signals the selected set can supply, from the evidence itself."""
    return SignalAvailability(
        syllabus=any(
            entry.in_syllabus
            or entry.in_course_topics
            or entry.syllabus_weight_percent is not None
            for entry in signals
        ),
        past_exam=any(entry.past_exam_question_count > 0 for entry in signals),
        mastery=any(entry.mastery_questions_answered > 0 for entry in signals),
        material=any(
            entry.material_chunk_count > 0 or entry.material_character_count > 0
            for entry in signals
        ),
    )


def _syllabus_scale(signals: Sequence[TopicSignals]) -> tuple[float, int, str]:
    declared = [
        entry.syllabus_weight_percent
        for entry in signals
        if entry.syllabus_weight_percent is not None
    ]
    mentions = [entry.syllabus_mention_count for entry in signals if entry.in_syllabus]
    max_declared = max(declared) if declared else 0.0
    max_mentions = max(mentions) if mentions else 0
    basis = BASIS_DECLARED_WEIGHT if max_declared > 0 else BASIS_MENTION_COUNT
    return max_declared, max_mentions, basis


def _past_exam_scale(signals: Sequence[TopicSignals]) -> tuple[list[float], str]:
    marks = [entry.past_exam_marks_total or 0.0 for entry in signals]
    if sum(marks) > 0:
        return marks, BASIS_MARKS
    return [float(entry.past_exam_question_count) for entry in signals], (
        BASIS_QUESTION_COUNT
    )


def _material_scale(signals: Sequence[TopicSignals]) -> tuple[list[float], str]:
    characters = [float(entry.material_character_count) for entry in signals]
    if sum(characters) > 0:
        return characters, BASIS_CHARACTERS
    return [float(entry.material_chunk_count) for entry in signals], BASIS_CHUNKS


def _syllabus_signal(
    entry: TopicSignals, max_declared: float, max_mentions: int
) -> tuple[float, float | None, str]:
    if entry.syllabus_weight_percent is not None and max_declared > 0:
        return (
            entry.syllabus_weight_percent / max_declared,
            entry.syllabus_weight_percent,
            BASIS_DECLARED_WEIGHT,
        )
    if entry.in_syllabus:
        share = entry.syllabus_mention_count / max_mentions if max_mentions > 0 else 0.0
        value = SYLLABUS_LISTED_BASE + (1 - SYLLABUS_LISTED_BASE) * share
        return value, float(entry.syllabus_mention_count), BASIS_MENTION_COUNT
    if entry.in_course_topics:
        return SYLLABUS_STUDENT_DECLARED, None, BASIS_STUDENT_DECLARED
    return 0.0, 0.0, BASIS_NO_EVIDENCE


def _mastery_signal(entry: TopicSignals) -> tuple[float, float | None, str, int | None]:
    answered = entry.mastery_questions_answered
    if answered == 0:
        return UNATTEMPTED_MASTERY_GAP, None, BASIS_NEUTRAL_PRIOR, None
    percentage = round(entry.mastery_questions_correct / answered * 100)
    gap = (100 - percentage) / 100
    confidence = min(1.0, answered / MASTERY_CONFIDENCE_QUESTIONS)
    value = UNATTEMPTED_MASTERY_GAP + confidence * (gap - UNATTEMPTED_MASTERY_GAP)
    return value, float(percentage), BASIS_ANSWERED, percentage


def _reason_codes(
    entry: TopicSignals,
    components: Mapping[str, int],
    availability: SignalAvailability,
    *,
    mastery_percentage: int | None,
    has_any_evidence: bool,
) -> tuple[str, ...]:
    codes: set[str] = set()

    if entry.is_high_priority:
        codes.add("student_marked_priority")

    if availability.syllabus:
        if entry.syllabus_weight_percent is not None:
            codes.add("syllabus_weighted")
        elif entry.in_syllabus:
            codes.add("syllabus_listed")
        elif entry.in_course_topics:
            codes.add("student_declared_topic")
    else:
        codes.add("syllabus_unavailable")

    if availability.past_exam:
        if components[SIGNAL_PAST_EXAM] >= REASON_STRONG_COMPONENT:
            codes.add("past_exam_frequent")
        elif entry.past_exam_question_count > 0:
            codes.add("past_exam_present")
    else:
        codes.add("past_exam_unavailable")

    if availability.mastery:
        if mastery_percentage is None:
            codes.add("not_yet_practised")
        elif mastery_percentage < NEEDS_REVIEW_THRESHOLD:
            codes.add("mastery_weak")
        elif mastery_percentage < MASTERED_THRESHOLD:
            codes.add("mastery_partial")
        else:
            codes.add("mastery_strong")
    else:
        codes.add("mastery_unavailable")

    if availability.material:
        if components[SIGNAL_MATERIAL] >= REASON_STRONG_COMPONENT:
            codes.add("material_heavy")
        elif entry.material_chunk_count == 0 and entry.material_character_count == 0:
            codes.add("no_material_found")
        elif components[SIGNAL_MATERIAL] < REASON_THIN_COMPONENT:
            codes.add("material_thin")
    else:
        codes.add("material_unavailable")

    if not has_any_evidence:
        codes.add("insufficient_evidence")

    return tuple(code for code in REASON_ORDER if code in codes)


def build_explanation(
    reason_codes: Sequence[str],
    entry: TopicSignals,
    *,
    mastery_percentage: int | None,
) -> str:
    """Phrase the top reasons from constants, never from a provider.

    The display label is deliberately not interpolated. A model-authored label
    containing a full stop would forge sentence structure inside student-facing
    prose; the label belongs in its own element on the screen.
    """
    values = {
        "syllabus_weight_percent": entry.syllabus_weight_percent or 0,
        "past_exam_question_count": entry.past_exam_question_count,
        "mastery_percentage": mastery_percentage
        if mastery_percentage is not None
        else 0,
    }
    chosen = [code for code in REASON_ORDER if code in set(reason_codes)]
    sentences = [
        REASON_SENTENCES[code].format(**values)
        for code in chosen[:MAX_EXPLANATION_REASONS]
        if code in REASON_SENTENCES
    ]
    return " ".join(sentences)


def rank_topics(
    signals: Sequence[TopicSignals],
    availability: SignalAvailability | None = None,
) -> RankingResult:
    """Order the selected topics and explain every placement.

    Pure: the same inputs always produce the same output, in the same order,
    with the same integers. Nothing here reads a database, a provider, or a
    clock.
    """
    entries = list(signals)
    resolved = (
        availability if availability is not None else derive_availability(entries)
    )
    weights = effective_weights(resolved)

    if not entries:
        return RankingResult(
            topics=(),
            effective_weights=weights,
            signals_available=resolved.as_dict(),
            signal_bases={},
        )

    max_declared, max_mentions, syllabus_basis = _syllabus_scale(entries)
    past_exam_values, past_exam_basis = _past_exam_scale(entries)
    material_values, material_basis = _material_scale(entries)
    max_past_exam = max(past_exam_values) if past_exam_values else 0.0
    max_material = max(material_values) if material_values else 0.0

    bases = {
        SIGNAL_SYLLABUS: syllabus_basis,
        SIGNAL_PAST_EXAM: past_exam_basis,
        SIGNAL_MASTERY: BASIS_ANSWERED,
        SIGNAL_MATERIAL: material_basis,
    }

    scored: list[tuple[tuple, RankedTopic, int]] = []
    for index, entry in enumerate(entries):
        syllabus_value, syllabus_raw, syllabus_row_basis = _syllabus_signal(
            entry, max_declared, max_mentions
        )
        past_exam_raw = past_exam_values[index]
        past_exam_value = past_exam_raw / max_past_exam if max_past_exam > 0 else 0.0
        material_raw = material_values[index]
        material_value = material_raw / max_material if max_material > 0 else 0.0
        (
            mastery_value,
            mastery_raw,
            mastery_row_basis,
            mastery_percentage,
        ) = _mastery_signal(entry)

        normalized = {
            SIGNAL_SYLLABUS: syllabus_value,
            SIGNAL_PAST_EXAM: past_exam_value,
            SIGNAL_MASTERY: mastery_value,
            SIGNAL_MATERIAL: material_value,
        }
        raw_values = {
            SIGNAL_SYLLABUS: syllabus_raw,
            SIGNAL_PAST_EXAM: past_exam_raw,
            SIGNAL_MASTERY: mastery_raw,
            SIGNAL_MATERIAL: material_raw,
        }
        row_bases = {
            SIGNAL_SYLLABUS: syllabus_row_basis,
            SIGNAL_PAST_EXAM: bases[SIGNAL_PAST_EXAM],
            SIGNAL_MASTERY: mastery_row_basis,
            SIGNAL_MATERIAL: bases[SIGNAL_MATERIAL],
        }

        components = {
            signal: _round_half_up(normalized[signal] * 100) for signal in SIGNAL_ORDER
        }
        breakdown = {
            signal: SignalBreakdown(
                available=resolved.get(signal),
                raw_value=raw_values[signal] if resolved.get(signal) else None,
                normalized_value=components[signal] if resolved.get(signal) else None,
                effective_weight=weights[signal],
                basis=row_bases[signal] if resolved.get(signal) else BASIS_NO_EVIDENCE,
            )
            for signal in SIGNAL_ORDER
        }

        total = sum(components[signal] * weights[signal] for signal in SIGNAL_ORDER)
        priority_score = (total + 50) // 100

        # Observed evidence only. Appearing in the student's own topic list is
        # a declaration of scope, not an observation about the course, so it
        # must not stop a topic being reported as unevidenced.
        has_any_evidence = any(
            (
                resolved.syllabus
                and (entry.in_syllabus or entry.syllabus_weight_percent is not None),
                resolved.past_exam and entry.past_exam_question_count > 0,
                resolved.mastery and entry.mastery_questions_answered > 0,
                resolved.material
                and (
                    entry.material_chunk_count > 0 or entry.material_character_count > 0
                ),
            )
        )

        reason_codes = _reason_codes(
            entry,
            components,
            resolved,
            mastery_percentage=mastery_percentage,
            has_any_evidence=has_any_evidence,
        )

        topic = RankedTopic(
            topic_key=entry.topic_key,
            display_label=entry.display_label,
            rank=0,
            is_high_priority=entry.is_high_priority,
            priority_score=priority_score,
            priority_band=BAND_LOW,
            has_any_evidence=has_any_evidence,
            is_unattempted=entry.mastery_questions_answered == 0,
            mastery_percentage=mastery_percentage,
            signals=breakdown,
            reason_codes=reason_codes,
            explanation=build_explanation(
                reason_codes, entry, mastery_percentage=mastery_percentage
            ),
        )

        sort_key = (
            0 if entry.is_high_priority else 1,
            0 if has_any_evidence else 1,
            -priority_score,
            -components[SIGNAL_PAST_EXAM],
            -components[SIGNAL_SYLLABUS],
            -components[SIGNAL_MASTERY],
            -components[SIGNAL_MATERIAL],
            entry.syllabus_position,
            entry.display_label.casefold(),
            entry.topic_key,
        )
        scored.append((sort_key, topic, priority_score))

    scored.sort(key=lambda item: item[0])

    # The band is a property of the evidence, so it is capped over score order
    # rather than over the final order. The student-priority tier moves a topic
    # up the list without changing what the evidence says about it, and a topic
    # scoring 93 must never be labelled medium because an override outranked it.
    cap = max(
        HIGH_PRIORITY_MIN_COUNT,
        len(scored) * HIGH_PRIORITY_MAX_SHARE_PERCENT // 100,
    )
    cleared = any(score >= HIGH_PRIORITY_SCORE for _, _, score in scored)
    by_score = sorted(
        range(len(scored)),
        key=lambda index: (-scored[index][2], scored[index][0]),
    )
    score_position = {index: place for place, index in enumerate(by_score, start=1)}

    ranked: list[RankedTopic] = []
    for position, (index, (_, topic, score)) in enumerate(
        ((index, item) for index, item in enumerate(scored)), start=1
    ):
        place = score_position[index]
        if topic.is_high_priority:
            band = BAND_HIGH
        elif score >= HIGH_PRIORITY_SCORE and place <= cap:
            band = BAND_HIGH
        elif place == 1 and not cleared and score > 0:
            band = BAND_HIGH
        elif score >= MEDIUM_PRIORITY_SCORE:
            band = BAND_MEDIUM
        else:
            band = BAND_LOW
        ranked.append(
            RankedTopic(
                topic_key=topic.topic_key,
                display_label=topic.display_label,
                rank=position,
                is_high_priority=topic.is_high_priority,
                priority_score=topic.priority_score,
                priority_band=band,
                has_any_evidence=topic.has_any_evidence,
                is_unattempted=topic.is_unattempted,
                mastery_percentage=topic.mastery_percentage,
                signals=topic.signals,
                reason_codes=topic.reason_codes,
                explanation=topic.explanation,
            )
        )

    return RankingResult(
        topics=tuple(ranked),
        effective_weights=weights,
        signals_available=resolved.as_dict(),
        signal_bases={
            signal: bases[signal] for signal in SIGNAL_ORDER if resolved.get(signal)
        },
    )
