from pydantic import BaseModel


class BoundedContext(BaseModel):
    context_truncated: bool
    chunks_used: int
    chunks_available: int


class RetrievedContext(BoundedContext):
    """Reporting for material chosen by semantic retrieval.

    ``context_truncated`` here means the character budget dropped a chunk that
    retrieval had already selected. Retrieval returning a subset of the corpus is
    the normal case rather than a truncation, so ``retrieval_narrowed`` carries
    that signal separately.
    """

    retrieval_narrowed: bool
    lowest_similarity: float | None = None
    highest_similarity: float | None = None
