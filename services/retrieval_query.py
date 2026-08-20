"""Turning a generation request into the query retrieval ranks against.

Every retrieval-backed feature asks the same question of a request: what text
should be embedded and ranked? The answer is the student's topic focus, except
when there isn't one. The sentinel and a blank focus both mean "the whole
course": embedding the literal words would rank nothing usefully, and an empty
query is not a query at all, so both expand to a description of the course.

The module reads no settings and touches no database. It is pure text handling,
shared so that a change to how "the whole course" is described cannot drift
between features.
"""

ALL_TOPICS_SENTINEL = "All Topics"

RETRIEVAL_QUERY_MAX_CHARS = 500


def course_descriptor(course) -> str:
    """Describe a course from whatever fields it actually has filled in."""
    descriptors = (
        getattr(course, "title", None),
        getattr(course, "description", None),
        getattr(course, "syllabus", None),
    )
    return ". ".join(
        " ".join(part.split()) for part in descriptors if part and part.strip()
    )


def build_retrieval_query(
    course,
    topic_focus: str,
    *,
    suffix: str = "",
    max_chars: int = RETRIEVAL_QUERY_MAX_CHARS,
) -> str:
    """Build the bounded query text for one generation request.

    ``suffix`` is appended emphasis a feature may add, such as exam-preparation
    terms. Room for it is reserved before trimming: a syllabus is unbounded
    text, so appending the suffix and trimming afterwards would drop it from
    exactly the courses with the most material.
    """
    topic = topic_focus.strip()
    if not topic or topic.casefold() == ALL_TOPICS_SENTINEL.casefold():
        topic = course_descriptor(course) or ALL_TOPICS_SENTINEL

    budget = max_chars - (len(suffix) + 1 if suffix else 0)
    topic = topic[:budget].strip()

    return f"{topic} {suffix}".strip() if suffix else topic
