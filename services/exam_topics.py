"""Stable identity for exam topics, independent of who wrote the label.

Exam Mode discovers topics from three vocabularies that have never agreed with
each other: ``course_topics.name`` is student-entered, ``quiz_questions.topic``
is model-generated free text, and the analysis model invents its own labels for
what it reads in the material. Ranking a topic against its own mastery requires
one identity all three resolve to.

That identity is ``canonical_topic_key``: a pure function of one string. It
never sees the model output, the database, or the run, which is what lets a
mastery label the model never read be matched at plan time, and what makes two
analyses over the same corpus produce the same keys.

The division of labour with the provider is exact. The model supplies semantics
only, returning a label and the surface forms it actually saw; it returns no
key, no score, and no ordering. Python supplies identity. The merge pass is
deterministic but consumes the model's aliases, so the model's knowledge widens
an equivalence class without ever being trusted to name it.

This module is pure: no database, no settings, no provider, no clock.
"""

import sys
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace

TOPIC_KEY_VERSION = 1

MAX_TOPIC_KEY_CHARS = 120
MAX_ALIASES = 12
TOPIC_KEY_SEPARATOR = "-"
MIN_CONTAINMENT_TOKENS = 2

# A bare number after one of these carries no topic identity: "Week 3: Graph
# Traversal" is the same topic as "Graph Traversal". The check is positional
# rather than a blanket rule against digits, because "Type 1 Diabetes" and
# "Type 2 Diabetes" are two topics and must stay two.
ORDINAL_CONTEXT_WORDS = frozenset(
    {"week", "lecture", "chapter", "unit", "part", "section", "module", "topic"}
)

TOPIC_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "the",
        "of",
        "for",
        "to",
        "in",
        "on",
        "with",
        "its",
        "their",
        "introduction",
        "intro",
        "overview",
        "basics",
        "basic",
        "fundamental",
        "fundamentals",
        "concept",
        "concepts",
        "topic",
        "topics",
        "chapter",
        "unit",
        "part",
        "section",
        "week",
        "lecture",
        "module",
    }
)

IRREGULAR_SINGULARS = {
    "matrices": "matrix",
    "indices": "index",
    "vertices": "vertex",
    "axes": "axis",
    "analyses": "analysis",
    "bases": "basis",
    "hypotheses": "hypothesis",
    "theses": "thesis",
    "formulae": "formula",
    "criteria": "criterion",
    "phenomena": "phenomenon",
    "data": "datum",
}

_PLURAL_ES_ENDINGS = ("sses", "shes", "ches", "xes", "zes")
_PROTECTED_S_ENDINGS = ("ss", "us", "is")


def _singularize(token: str) -> str:
    """Reduce one token to a singular form using a closed, conservative ruleset.

    Deliberately not a Porter or Snowball stemmer. A stemmer would merge
    ``matrices`` with ``matrix`` but also ``organization`` with ``organ``, and
    collapsing two genuinely distinct concepts is a worse failure here than
    leaving two spellings of one concept apart.
    """
    irregular = IRREGULAR_SINGULARS.get(token)
    if irregular is not None:
        return irregular
    if len(token) > 3:
        if token.endswith("ies"):
            return token[:-3] + "y"
        if token.endswith(_PLURAL_ES_ENDINGS):
            return token[:-2]
        if token.endswith("s") and not token.endswith(_PROTECTED_S_ENDINGS):
            return token[:-1]
    return token


def topic_tokens(label: object) -> tuple[str, ...]:
    """The sorted, deduplicated identity tokens of one topic label."""
    if not isinstance(label, str) or not label.strip():
        return ()

    decomposed = unicodedata.normalize("NFKD", label)
    stripped = "".join(
        character for character in decomposed if unicodedata.category(character) != "Mn"
    )
    folded = stripped.casefold()
    spaced = "".join(character if character.isalnum() else " " for character in folded)

    raw = spaced.split()
    kept: list[str] = []
    for index, token in enumerate(raw):
        if token in TOPIC_STOPWORDS:
            continue
        if token.isdigit() and index > 0 and raw[index - 1] in ORDINAL_CONTEXT_WORDS:
            continue
        kept.append(_singularize(token))

    tokens = [token for token in kept if token and token not in TOPIC_STOPWORDS]
    if not tokens:
        return ()
    return tuple(sorted(set(tokens)))


def canonical_topic_key(label: object) -> str:
    """The stable identity of one topic label, or ``""`` when there is none.

    Tokens are sorted rather than kept in written order, which is what makes
    ``Graph Traversal``, ``graph traversals``, and ``Traversal of Graphs`` one
    topic while leaving ``Binary Search`` and ``Binary Search Tree`` apart. The
    known limit is that it cannot distinguish A-of-B from B-of-A; within one
    course's topic list that phrasing does not occur.
    """
    tokens = topic_tokens(label)
    if not tokens:
        return ""

    kept: list[str] = []
    length = 0
    for token in tokens:
        addition = len(token) + (len(TOPIC_KEY_SEPARATOR) if kept else 0)
        if length + addition > MAX_TOPIC_KEY_CHARS:
            break
        kept.append(token)
        length += addition
    if not kept:
        return tokens[0][:MAX_TOPIC_KEY_CHARS]
    return TOPIC_KEY_SEPARATOR.join(kept)


@dataclass(frozen=True, slots=True)
class TopicEvidence:
    """Everything one analysis observed about one candidate topic."""

    in_syllabus: bool = False
    in_course_topics: bool = False
    in_past_exams: bool = False
    in_material: bool = False
    discovery_confidence: float = 0.5
    syllabus_weight_percent: float | None = None
    syllabus_mention_count: int = 0
    material_chunk_count: int = 0
    material_character_count: int = 0
    past_exam_question_count: int = 0
    past_exam_marks_total: float | None = None


@dataclass(frozen=True, slots=True)
class KeyedCandidate:
    """One canonical candidate topic, after keying and merging."""

    topic_key: str
    display_label: str
    aliases: tuple[str, ...]
    evidence: TopicEvidence
    citation_keys: tuple[str, ...] = ()
    alias_keys: tuple[str, ...] = field(default=())


@dataclass(frozen=True, slots=True)
class RawCandidate:
    """One candidate as the provider returned it, before keying."""

    label: str
    aliases: tuple[str, ...] = ()
    evidence: TopicEvidence = TopicEvidence()
    citation_keys: tuple[str, ...] = ()


def _alias_keys(label: str, aliases: Iterable[str]) -> tuple[str, ...]:
    own = canonical_topic_key(label)
    keys = {
        key
        for key in (canonical_topic_key(alias) for alias in aliases)
        if key and key != own
    }
    return tuple(sorted(keys))


def _merge_evidence(left: TopicEvidence, right: TopicEvidence) -> TopicEvidence:
    marks = [
        value
        for value in (left.past_exam_marks_total, right.past_exam_marks_total)
        if value is not None
    ]
    weights = [
        value
        for value in (left.syllabus_weight_percent, right.syllabus_weight_percent)
        if value is not None
    ]
    return TopicEvidence(
        in_syllabus=left.in_syllabus or right.in_syllabus,
        in_course_topics=left.in_course_topics or right.in_course_topics,
        in_past_exams=left.in_past_exams or right.in_past_exams,
        in_material=left.in_material or right.in_material,
        discovery_confidence=max(left.discovery_confidence, right.discovery_confidence),
        # A weight the syllabus declared once must not be doubled by a merge.
        syllabus_weight_percent=max(weights) if weights else None,
        syllabus_mention_count=left.syllabus_mention_count
        + right.syllabus_mention_count,
        material_chunk_count=left.material_chunk_count + right.material_chunk_count,
        material_character_count=left.material_character_count
        + right.material_character_count,
        past_exam_question_count=left.past_exam_question_count
        + right.past_exam_question_count,
        past_exam_marks_total=sum(marks) if marks else None,
    )


def _confidence_rank(candidate: KeyedCandidate) -> int:
    return round(candidate.evidence.discovery_confidence * 1000)


def key_candidates(raw: Sequence[RawCandidate]) -> tuple[KeyedCandidate, ...]:
    """Key, merge, and order the provider's candidates. Pure and total.

    Two candidates merge when their keys collide, or when one candidate's key
    appears among the other's alias keys. Alias-driven merging is the only
    place the model's semantics enter; the key itself never depends on them.
    """
    keyed: list[KeyedCandidate] = []
    for entry in raw:
        topic_key = canonical_topic_key(entry.label)
        if not topic_key:
            continue
        keyed.append(
            KeyedCandidate(
                topic_key=topic_key,
                display_label=entry.label.strip(),
                aliases=tuple(
                    alias.strip() for alias in entry.aliases if alias.strip()
                ),
                evidence=entry.evidence,
                citation_keys=tuple(entry.citation_keys),
                alias_keys=_alias_keys(entry.label, entry.aliases),
            )
        )
    if not keyed:
        return ()

    parent = list(range(len(keyed)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    for left in range(len(keyed)):
        for right in range(left + 1, len(keyed)):
            first, second = keyed[left], keyed[right]
            if (
                first.topic_key == second.topic_key
                or second.topic_key in first.alias_keys
                or first.topic_key in second.alias_keys
            ):
                union(left, right)

    groups: dict[int, list[int]] = {}
    for index in range(len(keyed)):
        groups.setdefault(find(index), []).append(index)

    merged: list[KeyedCandidate] = []
    for members in groups.values():
        winner_index = min(
            members,
            key=lambda index: (
                -_confidence_rank(keyed[index]),
                index,
                keyed[index].display_label,
            ),
        )
        winner = keyed[winner_index]

        evidence = winner.evidence
        aliases: set[str] = set(winner.aliases)
        alias_keys: set[str] = set(winner.alias_keys)
        citation_keys: list[str] = list(winner.citation_keys)
        topic_key = winner.topic_key

        for index in members:
            if index == winner_index:
                continue
            other = keyed[index]
            evidence = _merge_evidence(evidence, other.evidence)
            aliases.update(other.aliases)
            aliases.add(other.display_label)
            alias_keys.update(other.alias_keys)
            alias_keys.add(other.topic_key)
            citation_keys.extend(other.citation_keys)
            topic_key = min(topic_key, other.topic_key)

        alias_keys.discard(topic_key)
        merged.append(
            replace(
                winner,
                topic_key=topic_key,
                evidence=evidence,
                aliases=tuple(sorted(aliases))[:MAX_ALIASES],
                alias_keys=tuple(sorted(alias_keys)),
                citation_keys=tuple(dict.fromkeys(citation_keys)),
            )
        )

    merged.sort(
        key=lambda candidate: (-_confidence_rank(candidate), candidate.topic_key)
    )
    return tuple(merged)


def build_topic_index(candidates: Sequence[object]) -> dict[str, str]:
    """Map every known key and alias key onto the candidate that owns it.

    A key two candidates both claim is dropped rather than assigned, because a
    label that matches two topics is evidence of neither.
    """
    owners: dict[str, set[str]] = {}
    for candidate in candidates:
        topic_key = getattr(candidate, "topic_key", "")
        if not topic_key:
            continue
        owners.setdefault(topic_key, set()).add(topic_key)
        aliases = getattr(candidate, "alias_keys", None)
        if aliases is None:
            aliases = [
                canonical_topic_key(alias)
                for alias in (getattr(candidate, "aliases", None) or [])
            ]
        for alias_key in aliases:
            if alias_key and alias_key != topic_key:
                owners.setdefault(alias_key, set()).add(topic_key)

    return {key: next(iter(owned)) for key, owned in owners.items() if len(owned) == 1}


def match_topic_key(label: object, index: Mapping[str, str]) -> str | None:
    """Resolve a free-text topic label onto a known candidate key.

    Exact identity first. Failing that, a candidate whose tokens are a proper
    subset of the label's tokens can claim it, but only if the candidate has at
    least two tokens and no equally specific candidate also claims it: guessing
    between two plausible owners is the merge error this module exists to
    avoid, so an ambiguous label matches nothing at all.
    """
    key = canonical_topic_key(label)
    if not key:
        return None

    exact = index.get(key)
    if exact is not None:
        return exact

    label_tokens = set(topic_tokens(label))
    if not label_tokens:
        return None

    best_size = 0
    best_owner: str | None = None
    ambiguous = False
    for candidate_key, owner in index.items():
        tokens = set(candidate_key.split(TOPIC_KEY_SEPARATOR))
        if len(tokens) < MIN_CONTAINMENT_TOKENS or not tokens < label_tokens:
            continue
        if len(tokens) > best_size:
            best_size, best_owner, ambiguous = len(tokens), owner, False
        elif len(tokens) == best_size and owner != best_owner:
            ambiguous = True

    if ambiguous:
        return None
    return best_owner


def syllabus_positions(
    candidates: Sequence[object], syllabus: str | None
) -> dict[str, int]:
    """Where each candidate first appears in the syllabus, for tie-breaking.

    A candidate the syllabus never names gets ``sys.maxsize``, which sorts it
    after every candidate the syllabus did name without claiming a position it
    does not have.
    """
    if not syllabus or not syllabus.strip():
        return {}

    folded = syllabus.casefold()
    positions: dict[str, int] = {}
    for candidate in candidates:
        topic_key = getattr(candidate, "topic_key", "")
        if not topic_key:
            continue
        surfaces = [getattr(candidate, "display_label", "")]
        surfaces.extend(getattr(candidate, "aliases", None) or [])
        found = [
            folded.find(surface.casefold())
            for surface in surfaces
            if surface and surface.strip()
        ]
        hits = [offset for offset in found if offset >= 0]
        positions[topic_key] = min(hits) if hits else sys.maxsize
    return positions
