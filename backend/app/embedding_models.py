from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EmbeddingModelSpec:
    model_id: str
    dimensions: int
    query_prefix: str
    passage_prefix: str
    max_sequence_length: int


EMBEDDING_MODEL = EmbeddingModelSpec(
    model_id="intfloat/multilingual-e5-large",
    dimensions=1024,
    query_prefix="query: ",
    passage_prefix="passage: ",
    max_sequence_length=512,
)

# Pinned by migration a1f6c3b7d284. A model of another width needs a new
# revision, not an edit here.
SCHEMA_EMBEDDING_DIMENSIONS = 1024
