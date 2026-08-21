from __future__ import annotations

import re
from dataclasses import dataclass

from schemas.prompt_context import EDUCATION_LEVEL_DIRECTIVES, EducationLevel

_LEVEL = (
    r"(?:universit(?:y|ies)|college|undergraduate|graduate"
    r"|post[-\s]*graduate|high[-\s]*school|secondary[-\s]*school)"
)
_ROLE = (
    r"(?:students?|learners?|level|tutor|instructor|professor"
    r"|teaching\s+assistant|assistant|audience|course|curriculum)"
)
_DISCIPLINE = r"(?:computer\s+science|\bCS\b)"

_LEVEL_GUIDANCE = (
    "Learner level must come from the EDUCATION_LEVEL variable, never from a "
    "fixed role or audience written into the prompt."
)
_DISCIPLINE_GUIDANCE = (
    "Discipline must come from the SUBJECT_AREA and COURSE_TITLE variables, "
    "never from a fixed subject role written into the prompt."
)
_MATERIAL_GUIDANCE = (
    "Material type must come from the MATERIAL_KIND variable, never from a "
    "prompt that assumes every source is one kind."
)


@dataclass(frozen=True)
class NeutralityRule:
    name: str
    pattern: re.Pattern[str]
    guidance: str


@dataclass(frozen=True)
class Violation:
    rule: NeutralityRule
    matched: str
    line: int


def _rule(name: str, pattern: str, guidance: str) -> NeutralityRule:
    return NeutralityRule(name, re.compile(pattern, re.IGNORECASE), guidance)


NEUTRALITY_RULES: tuple[NeutralityRule, ...] = (
    _rule("level_role", rf"{_LEVEL}[-\s]+{_ROLE}", _LEVEL_GUIDANCE),
    _rule("level_for", rf"\bfor\s+(?:an?\s+|the\s+)?{_LEVEL}\b", _LEVEL_GUIDANCE),
    _rule(
        "level_persona",
        rf"\byou\s+are\s+an?\b[^.]{{0,60}}{_LEVEL}",
        _LEVEL_GUIDANCE,
    ),
    _rule("discipline_role", rf"{_DISCIPLINE}[-\s]+{_ROLE}", _DISCIPLINE_GUIDANCE),
    _rule(
        "discipline_persona",
        rf"\byou\s+are\s+an?\b[^.]{{0,60}}{_DISCIPLINE}",
        _DISCIPLINE_GUIDANCE,
    ),
    _rule(
        "discipline_for",
        rf"\bfor\s+(?:an?\s+|the\s+)?{_DISCIPLINE}\b",
        _DISCIPLINE_GUIDANCE,
    ),
    _rule(
        "material_lecture_notes",
        r"\b(?:the|these|those|your|following)\s+lecture[-\s]*notes?\b",
        _MATERIAL_GUIDANCE,
    ),
)

LEVEL_VOCABULARY = re.compile(
    r"universit(?:y|ies)|college|undergraduate|graduate"
    r"|high[-\s]*school|secondary[-\s]*school",
    re.IGNORECASE,
)

DISCIPLINE_VOCABULARY = re.compile(r"computer\s+science|\bCS\b", re.IGNORECASE)

_TRANSFORMS = re.compile(r"['\"][ \t]*\n[ \t]*['\"]|\\n|\s+")


def _replacement_for(fragment: str) -> str:
    return "" if fragment[0] in "'\"" else " "


def normalize_with_offsets(text: str) -> tuple[str, list[int]]:
    parts: list[str] = []
    offsets: list[int] = []
    position = 0

    for match in _TRANSFORMS.finditer(text):
        parts.append(text[position : match.start()])
        offsets.extend(range(position, match.start()))
        replacement = _replacement_for(match.group(0))
        parts.append(replacement)
        offsets.extend([match.start()] * len(replacement))
        position = match.end()

    parts.append(text[position:])
    offsets.extend(range(position, len(text)))
    return "".join(parts), offsets


def normalize(text: str) -> str:
    return normalize_with_offsets(text)[0]


def blank_out(normalized: str, exempt: list[str]) -> str:
    for fragment in exempt:
        if fragment:
            normalized = normalized.replace(fragment, " " * len(fragment))
    return normalized


def find_neutrality_violations(
    text: str, exempt: list[str] | None = None
) -> list[Violation]:
    normalized, offsets = normalize_with_offsets(text)
    scannable = blank_out(normalized, exempt or [])

    violations: list[Violation] = []
    for rule in NEUTRALITY_RULES:
        for match in rule.pattern.finditer(scannable):
            origin = offsets[match.start()] if match.start() < len(offsets) else 0
            violations.append(
                Violation(
                    rule=rule,
                    matched=normalized[match.start() : match.end()],
                    line=text.count("\n", 0, origin) + 1,
                )
            )
    return violations


def describe(surface: str, violations: list[Violation], with_lines: bool) -> str:
    lines = [f"Prompt neutrality violation in {surface}:"]
    for violation in violations:
        location = f":{violation.line}" if with_lines else ""
        lines.append(
            f"  {surface}{location} [{violation.rule.name}] "
            f"matched {violation.matched!r} - {violation.rule.guidance}"
        )
    return "\n".join(lines)


def assert_source_neutral(
    text: str,
    surface: str,
    exempt: list[str] | None = None,
    with_lines: bool = False,
) -> None:
    violations = find_neutrality_violations(text, exempt)
    assert not violations, describe(surface, violations, with_lines)


def assert_no_level_or_discipline_fallback(rendered: str, surface: str) -> None:
    normalized = normalize(rendered)

    level_hits = sorted(
        {m.group(0).lower() for m in LEVEL_VOCABULARY.finditer(normalized)}
    )
    assert not level_hits, (
        f"{surface} resolved an unspecified education level into a concrete "
        f"academic tier: {level_hits}. An unknown level must stay neutral."
    )

    discipline_hits = sorted(
        {m.group(0).lower() for m in DISCIPLINE_VOCABULARY.finditer(normalized)}
    )
    assert not discipline_hits, (
        f"{surface} resolved an unspecified subject area into a concrete "
        f"discipline: {discipline_hits}. An unknown subject must stay neutral."
    )


def assert_only_the_supplied_level_directive(
    rendered: str, level: EducationLevel, surface: str
) -> None:
    assert EDUCATION_LEVEL_DIRECTIVES[level] in rendered, (
        f"{surface} did not carry the directive for {level.value}"
    )
    for other, directive in EDUCATION_LEVEL_DIRECTIVES.items():
        if other is level:
            continue
        assert directive not in rendered, (
            f"{surface} was rendered for {level.value} but also carries the "
            f"{other.value} directive"
        )
