import logging
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from schemas.citation import Citation

logger = logging.getLogger(__name__)

MAX_DOCUMENT_LABEL_CHARS = 120
MAX_CITATIONS_PER_CLAIM = 4

CITATION_KEY_PREFIX = "S"

_FORBIDDEN_LABEL_CHARACTERS = re.compile(r"[\[\]\r\n]")
_LABEL_SEPARATORS = re.compile(r"[-_\s]+")
_CITATION_KEY = re.compile(r"^\[?\s*[Ss](\d{1,3})\s*\]?$")
_MARKER_GROUP = re.compile(r"\[([^\]\n]{1,40})\]")
_MARKER_SPLIT = re.compile(r"[,;]")
_CLOSING_PUNCTUATION = ".,;:!?)]}\"'"


@dataclass(frozen=True, slots=True)
class SuppliedCitation:
    key: str
    chunk_id: int
    document_id: UUID
    document_label: str
    page_start: int | None
    page_end: int | None

    @property
    def identity(self) -> tuple[UUID, int | None, int | None]:
        return (self.document_id, self.page_start, self.page_end)

    def as_citation(self) -> Citation:
        return Citation(
            key=self.key,
            document_id=self.document_id,
            document_label=self.document_label,
            page_start=self.page_start,
            page_end=self.page_end,
        )


@dataclass(frozen=True)
class CitedAnswer:
    text: str
    citations: list[Citation]


def _prettify_token(token: str) -> str:
    if token.isdigit():
        return token.lstrip("0") or "0"
    if token.lower() == token:
        return token[:1].upper() + token[1:]
    return token


def document_label(file_name: str) -> str:
    stem = os.path.splitext(file_name)[0] or file_name
    cleaned = _FORBIDDEN_LABEL_CHARACTERS.sub("", stem)
    tokens = [token for token in _LABEL_SEPARATORS.split(cleaned) if token]
    label = " ".join(_prettify_token(token) for token in tokens)
    if not label:
        return _FORBIDDEN_LABEL_CHARACTERS.sub("", file_name)[:MAX_DOCUMENT_LABEL_CHARS]
    return label[:MAX_DOCUMENT_LABEL_CHARS]


def citation_key(position: int) -> str:
    return f"{CITATION_KEY_PREFIX}{position}"


def normalize_citation_key(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    match = _CITATION_KEY.match(value.strip())
    if match is None:
        return None
    return citation_key(int(match.group(1)))


def build_supplied_citations(
    chunks: Sequence[object], *, documents: Mapping[UUID, str]
) -> tuple[SuppliedCitation, ...]:
    supplied: list[SuppliedCitation] = []
    for position, chunk in enumerate(chunks, start=1):
        document_id = chunk.document_id
        supplied.append(
            SuppliedCitation(
                key=citation_key(position),
                chunk_id=chunk.chunk_id,
                document_id=document_id,
                document_label=document_label(documents.get(document_id, "")),
                page_start=chunk.page_number,
                page_end=chunk.end_page_number,
            )
        )
    return tuple(supplied)


def _page_suffix(page_start: int | None, page_end: int | None) -> str:
    if page_start is None:
        return ""
    if page_end is None or page_end == page_start:
        return f", p. {page_start}"
    return f", pp. {page_start}-{page_end}"


def citation_header(
    *, key: str, label: str, page_start: int | None, page_end: int | None
) -> str:
    return f"[{key}] ({label}{_page_suffix(page_start, page_end)})"


def resolve_citations(
    keys: Iterable[object], supplied: Mapping[str, SuppliedCitation]
) -> list[Citation]:
    resolved: list[Citation] = []
    seen_keys: set[str] = set()
    seen_identities: set[tuple[UUID, int | None, int | None]] = set()
    dropped = 0

    for raw in keys:
        key = normalize_citation_key(raw)
        if key is None:
            dropped += 1
            continue
        citation = supplied.get(key)
        if citation is None:
            dropped += 1
            continue
        if key in seen_keys or citation.identity in seen_identities:
            continue
        seen_keys.add(key)
        seen_identities.add(citation.identity)
        resolved.append(citation.as_citation())
        if len(resolved) == MAX_CITATIONS_PER_CLAIM:
            break

    if dropped:
        logger.debug("Dropped %d unresolvable citation keys", dropped)
    return resolved


def _group_keys(inner: str) -> list[str] | None:
    keys: list[str] = []
    for part in _MARKER_SPLIT.split(inner):
        key = normalize_citation_key(part)
        if key is None:
            return None
        keys.append(key)
    return keys


def _repair_spacing(parts: list[str], following: str) -> None:
    if not parts or not parts[-1].endswith(" "):
        return
    if (
        following == ""
        or following[0].isspace()
        or following[0] in _CLOSING_PUNCTUATION
    ):
        parts[-1] = parts[-1][:-1]


def sanitize_citation_markers(
    text: str, supplied: Mapping[str, SuppliedCitation]
) -> CitedAnswer:
    parts: list[str] = []
    citations: dict[str, Citation] = {}
    cursor = 0
    dropped = 0

    for match in _MARKER_GROUP.finditer(text):
        keys = _group_keys(match.group(1))
        if keys is None:
            continue

        parts.append(text[cursor : match.start()])
        cursor = match.end()

        survivors: list[str] = []
        identities: set[tuple[UUID, int | None, int | None]] = set()
        for key in keys:
            citation = supplied.get(key)
            if citation is None:
                dropped += 1
                continue
            if key in survivors or citation.identity in identities:
                continue
            survivors.append(key)
            identities.add(citation.identity)
            citations.setdefault(key, citation.as_citation())

        if survivors:
            parts.append("".join(f"[{key}]" for key in survivors))
        else:
            _repair_spacing(parts, text[cursor : cursor + 1])

    parts.append(text[cursor:])

    if dropped:
        logger.debug("Dropped %d unresolvable citation markers", dropped)
    return CitedAnswer(text="".join(parts), citations=list(citations.values()))


def strip_citation_markers(text: str) -> str:
    return sanitize_citation_markers(text, {}).text
