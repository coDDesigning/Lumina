from typing import Literal

from pydantic import BaseModel, ConfigDict


class BoundedContext(BaseModel):
    context_truncated: bool
    chunks_used: int
    chunks_available: int
    profile_knowledge_used: bool = False
    profile_knowledge_items_used: int = 0


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


class RetrievalGenerationContext(BaseModel):
    """What retrieval actually produced for one stored generation.

    Persisted as a versioned JSON document and read back permissively, so a row
    written by an older version can never fail a history read.
    """

    model_config = ConfigDict(extra="ignore")

    version: Literal[1] = 1
    chunks_ranked: int
    chunks_retrieved: int
    chunks_used: int
    chunks_available: int
    lowest_similarity: float | None = None
    highest_similarity: float | None = None
    truncated: bool
    profile_knowledge_used: bool = False
    profile_knowledge_items_used: int = 0
    profile_knowledge_characters_used: int = 0
    profile_knowledge_truncated: bool = False

    @classmethod
    def from_material(
        cls,
        material,
        profile_knowledge=None,
    ) -> "RetrievalGenerationContext":
        profile_used = profile_knowledge is not None and not profile_knowledge.is_empty
        return cls(
            chunks_ranked=material.chunks_ranked,
            chunks_retrieved=material.chunks_retrieved,
            chunks_used=material.chunks_used,
            chunks_available=material.chunks_available,
            lowest_similarity=material.lowest_similarity,
            highest_similarity=material.highest_similarity,
            truncated=material.truncated,
            profile_knowledge_used=profile_used,
            profile_knowledge_items_used=(
                profile_knowledge.items_used if profile_knowledge else 0
            ),
            profile_knowledge_characters_used=(
                len(profile_knowledge.text) if profile_knowledge else 0
            ),
            profile_knowledge_truncated=(
                profile_knowledge.truncated if profile_knowledge else False
            ),
        )
