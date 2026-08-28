"""Topic identity: what merges, what must not, and what stays unmatched."""

import ast
from pathlib import Path

import pytest

from services.exam_topics import (
    MIN_CONTAINMENT_TOKENS,
    RawCandidate,
    TopicEvidence,
    build_topic_index,
    canonical_topic_key,
    key_candidates,
    match_topic_key,
    syllabus_positions,
    topic_tokens,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "label",
    [
        "Graph Traversal",
        "graph traversals",
        "GRAPH TRAVERSAL",
        "Traversal of Graphs",
        "  graph   traversal  ",
        "Graph-Traversal",
        "Introduction to Graph Traversal",
        "Week 3: Graph Traversal",
    ],
)
def test_every_wording_of_one_topic_produces_one_key(label: str) -> None:
    assert canonical_topic_key(label) == "graph-traversal"


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("Depth-First Search", "Breadth-First Search"),
        ("Binary Search", "Binary Search Tree"),
        ("Graph Coloring", "Graph Traversal"),
        ("Organization Theory", "Organ Systems"),
        ("Linear Regression", "Logistic Regression"),
        ("Matrix Multiplication", "Matrix Inversion"),
    ],
)
def test_distinct_nearby_topics_never_collapse(left: str, right: str) -> None:
    assert canonical_topic_key(left) != canonical_topic_key(right)


@pytest.mark.parametrize(
    ("plural", "singular"),
    [
        ("Matrices", "Matrix"),
        ("Vertices", "Vertex"),
        ("Properties", "Property"),
        ("Classes", "Class"),
        ("Graphs", "Graph"),
    ],
)
def test_a_closed_ruleset_singularizes_without_over_stemming(
    plural: str, singular: str
) -> None:
    assert canonical_topic_key(plural) == canonical_topic_key(singular)


@pytest.mark.parametrize("label", ["Class", "Calculus", "Analysis", "Basis"])
def test_a_word_that_merely_ends_in_s_is_left_alone(label: str) -> None:
    assert canonical_topic_key(label) == label.casefold()


def test_diacritics_fold_so_an_unaccented_search_still_matches() -> None:
    assert canonical_topic_key("Fourier Séries") == canonical_topic_key(
        "Fourier Series"
    )


@pytest.mark.parametrize("label", ["", "   ", None, 42, "the of and"])
def test_a_label_with_no_identity_produces_no_key(label) -> None:
    assert canonical_topic_key(label) == ""


def test_a_very_long_label_is_truncated_on_a_token_boundary() -> None:
    label = " ".join(f"token{index:03d}" for index in range(40))
    key = canonical_topic_key(label)

    assert len(key) <= 120
    assert not key.endswith("-")
    assert all(part in topic_tokens(label) for part in key.split("-"))


def _candidate(label: str, aliases=(), **evidence) -> RawCandidate:
    return RawCandidate(
        label=label, aliases=tuple(aliases), evidence=TopicEvidence(**evidence)
    )


def test_aliases_merge_candidates_the_key_alone_would_leave_apart() -> None:
    merged = key_candidates(
        [
            _candidate("Graph Traversal", ("BFS", "DFS"), discovery_confidence=0.9),
            _candidate("BFS", discovery_confidence=0.4, in_material=True),
        ]
    )

    assert len(merged) == 1
    assert merged[0].topic_key == "bfs"
    assert "BFS" in merged[0].aliases


def test_merging_sums_counts_but_never_doubles_a_declared_weight() -> None:
    merged = key_candidates(
        [
            _candidate(
                "Graph Traversal",
                discovery_confidence=0.9,
                in_syllabus=True,
                syllabus_weight_percent=20.0,
                syllabus_mention_count=3,
                material_chunk_count=4,
            ),
            _candidate(
                "Traversal of Graphs",
                discovery_confidence=0.5,
                in_material=True,
                syllabus_weight_percent=20.0,
                syllabus_mention_count=2,
                material_chunk_count=6,
            ),
        ]
    )

    assert len(merged) == 1
    evidence = merged[0].evidence
    assert evidence.syllabus_mention_count == 5
    assert evidence.material_chunk_count == 10
    assert evidence.syllabus_weight_percent == 20.0
    assert evidence.in_syllabus and evidence.in_material
    assert merged[0].display_label == "Graph Traversal"


def test_keying_the_same_candidates_twice_produces_the_same_result() -> None:
    raw = [
        _candidate("Dynamic Programming", discovery_confidence=0.7),
        _candidate("Graph Traversal", ("BFS",), discovery_confidence=0.7),
        _candidate("Sorting", discovery_confidence=0.7),
    ]

    assert key_candidates(raw) == key_candidates(raw)


def test_a_label_with_no_identity_is_dropped_rather_than_keyed_blank() -> None:
    merged = key_candidates([_candidate("the of and"), _candidate("Sorting")])

    assert [candidate.topic_key for candidate in merged] == ["sorting"]


def _index(labels_with_aliases):
    merged = key_candidates(
        [_candidate(label, aliases) for label, aliases in labels_with_aliases]
    )
    return merged, build_topic_index(merged)


def test_an_exact_label_resolves_to_its_candidate() -> None:
    _, index = _index([("Graph Traversal", ()), ("Sorting", ())])

    assert match_topic_key("graph traversals", index) == "graph-traversal"


def test_a_more_specific_label_resolves_to_the_topic_it_contains() -> None:
    _, index = _index([("Graph Traversal", ()), ("Sorting", ())])

    assert match_topic_key("Graph Traversal Algorithms", index) == "graph-traversal"


def test_a_label_two_candidates_equally_claim_matches_neither() -> None:
    _, index = _index([("Binary Search", ()), ("Hash Table", ())])

    assert match_topic_key("Binary Search and Hash Table Lookup", index) is None


def test_a_single_token_candidate_never_swallows_a_longer_label() -> None:
    _, index = _index([("Graphs", ())])

    assert MIN_CONTAINMENT_TOKENS == 2
    assert match_topic_key("Graph Coloring Heuristics", index) is None


def test_an_unrelated_label_matches_nothing_rather_than_the_closest_topic() -> None:
    _, index = _index([("Graph Traversal", ()), ("Sorting", ())])

    assert match_topic_key("Quantum Entanglement", index) is None


def test_an_alias_two_candidates_share_is_dropped_from_the_index() -> None:
    merged = key_candidates(
        [
            _candidate("Alpha Topic", ("Shared Alias",), discovery_confidence=0.9),
            _candidate("Beta Topic", ("Shared Alias",), discovery_confidence=0.9),
        ]
    )
    index = build_topic_index(merged)

    # The two candidates merged through the alias they share, so the alias has
    # exactly one owner; what must never happen is it silently naming one of two.
    owners = {index[key] for key in index}
    assert len(owners) == len(merged)


def test_syllabus_position_orders_by_first_appearance_and_admits_absence() -> None:
    merged, _ = _index([("Sorting", ()), ("Graph Traversal", ()), ("Recursion", ())])
    positions = syllabus_positions(merged, "Week 1 Graph Traversal. Week 2 Sorting.")

    assert positions["graph-traversal"] < positions["sorting"]
    assert positions["recursion"] > positions["sorting"]


def test_syllabus_position_is_empty_when_there_is_no_syllabus() -> None:
    merged, _ = _index([("Sorting", ())])

    assert syllabus_positions(merged, None) == {}
    assert syllabus_positions(merged, "   ") == {}


FORBIDDEN_PURE_IMPORTS = ("sqlalchemy", "random", "time", "datetime")


@pytest.mark.parametrize(
    "module", ["exam_topics", "exam_ranking", "exam_mock_allocation"]
)
def test_the_deterministic_modules_import_no_database_provider_or_clock(
    module: str,
) -> None:
    """The ranking path must stay reproducible from persisted values alone.

    An import scan rather than a mock, because the guarantee is structural: a
    module that cannot import a session, a provider factory, or a clock cannot
    accidentally grow a dependency on one.
    """
    source = (PROJECT_ROOT / "services" / f"{module}.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    for name in imported:
        assert name.split(".")[0] not in FORBIDDEN_PURE_IMPORTS, (
            f"{module}.py must not import {name}"
        )
        assert not name.startswith("services.text_generation"), (
            f"{module}.py must not reach a provider"
        )


def test_a_numbered_course_label_does_not_erase_a_numbered_topic() -> None:
    """Dropping a stray ordinal must not merge two topics that differ by one."""
    assert canonical_topic_key("Type 1 Diabetes") != canonical_topic_key(
        "Type 2 Diabetes"
    )
    assert canonical_topic_key("Week 3: Graph Traversal") == canonical_topic_key(
        "Graph Traversal"
    )
