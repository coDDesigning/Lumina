"""The embedding stage of document processing.

Translates provider and vector-store failures into the classified, safe
processing errors the durable job state machine already understands, so an
embedding outage requeues the job instead of leaking provider text or letting
a document reach `ready` with no vectors behind it.
"""

from collections.abc import Sequence

from backend.app.models import EMBEDDING_DIMENSIONS
from services.document_extraction import DocumentProcessingError
from services.embeddings import (
    EmbeddingAuthError,
    EmbeddingConfigurationError,
    EmbeddingConnectionError,
    EmbeddingDimensionMismatchError,
    EmbeddingError,
    EmbeddingInvalidResponseError,
    EmbeddingProvider,
    EmbeddingProviderError,
    EmbeddingRateLimitError,
    EmbeddingTimeoutError,
    get_embedding_provider,
)
from services.vector_store import VectorStoreConfigurationError, VectorStoreError

EMBEDDING_STAGE = "generating_embeddings"

ERROR_EMBEDDING_TIMEOUT = "EMBEDDING_TIMEOUT"
ERROR_EMBEDDING_PROVIDER_UNAVAILABLE = "EMBEDDING_PROVIDER_UNAVAILABLE"
ERROR_EMBEDDING_RATE_LIMITED = "EMBEDDING_RATE_LIMITED"
ERROR_EMBEDDING_INVALID_RESPONSE = "EMBEDDING_INVALID_RESPONSE"
ERROR_EMBEDDING_DIMENSION_MISMATCH = "EMBEDDING_DIMENSION_MISMATCH"
ERROR_EMBEDDING_CONFIGURATION_INVALID = "EMBEDDING_CONFIGURATION_INVALID"
ERROR_VECTOR_PERSISTENCE_FAILED = "VECTOR_PERSISTENCE_FAILED"
ERROR_VECTOR_STORE_UNAVAILABLE = "VECTOR_STORE_UNAVAILABLE"

_PUBLIC_MESSAGES = {
    ERROR_EMBEDDING_TIMEOUT: "Embedding generation timed out.",
    ERROR_EMBEDDING_PROVIDER_UNAVAILABLE: (
        "The embedding provider is currently unavailable."
    ),
    ERROR_EMBEDDING_RATE_LIMITED: ("The embedding provider rejected the request rate."),
    ERROR_EMBEDDING_INVALID_RESPONSE: (
        "The embedding provider returned an unusable response."
    ),
    ERROR_EMBEDDING_DIMENSION_MISMATCH: (
        "The configured embedding model does not match the supported vector size."
    ),
    ERROR_EMBEDDING_CONFIGURATION_INVALID: (
        "The embedding provider is not configured correctly."
    ),
    ERROR_VECTOR_PERSISTENCE_FAILED: (
        "The generated embeddings could not be recorded."
    ),
    ERROR_VECTOR_STORE_UNAVAILABLE: "The vector store is currently unavailable.",
}

_EMBEDDING_ERROR_CODES: tuple[tuple[type[EmbeddingError], str, bool], ...] = (
    (EmbeddingTimeoutError, ERROR_EMBEDDING_TIMEOUT, True),
    (EmbeddingRateLimitError, ERROR_EMBEDDING_RATE_LIMITED, True),
    (EmbeddingConnectionError, ERROR_EMBEDDING_PROVIDER_UNAVAILABLE, True),
    (EmbeddingDimensionMismatchError, ERROR_EMBEDDING_DIMENSION_MISMATCH, False),
    (EmbeddingInvalidResponseError, ERROR_EMBEDDING_INVALID_RESPONSE, False),
    (EmbeddingAuthError, ERROR_EMBEDDING_CONFIGURATION_INVALID, False),
    (EmbeddingConfigurationError, ERROR_EMBEDDING_CONFIGURATION_INVALID, False),
    (EmbeddingProviderError, ERROR_EMBEDDING_PROVIDER_UNAVAILABLE, True),
)


def _processing_error(code: str, *, retryable: bool) -> DocumentProcessingError:
    return DocumentProcessingError(
        code,
        _PUBLIC_MESSAGES[code],
        retryable=retryable,
        failed_stage=EMBEDDING_STAGE,
    )


def classify_embedding_error(exc: Exception) -> DocumentProcessingError:
    """Map a provider or store failure onto a stable processing error."""
    if isinstance(exc, VectorStoreConfigurationError):
        return _processing_error(ERROR_VECTOR_STORE_UNAVAILABLE, retryable=False)
    if isinstance(exc, VectorStoreError):
        return _processing_error(ERROR_VECTOR_PERSISTENCE_FAILED, retryable=True)
    for error_type, code, retryable in _EMBEDDING_ERROR_CODES:
        if isinstance(exc, error_type):
            return _processing_error(code, retryable=retryable)
    if isinstance(exc, EmbeddingError):
        return _processing_error(
            ERROR_EMBEDDING_PROVIDER_UNAVAILABLE,
            retryable=bool(getattr(exc, "retryable", False)),
        )
    return _processing_error(ERROR_EMBEDDING_PROVIDER_UNAVAILABLE, retryable=True)


def embed_document_chunks(
    texts: Sequence[str],
    *,
    provider: EmbeddingProvider | None = None,
) -> list[list[float]]:
    """Return exactly one vector per chunk text, in input order."""
    if not texts:
        return []
    if provider is None:
        try:
            provider = get_embedding_provider()
        except Exception as exc:
            raise classify_embedding_error(exc) from exc

    try:
        vectors = provider.embed_documents(list(texts))
    except Exception as exc:
        raise classify_embedding_error(exc) from exc

    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise _processing_error(ERROR_EMBEDDING_INVALID_RESPONSE, retryable=False)
    for vector in vectors:
        if len(vector) != EMBEDDING_DIMENSIONS:
            raise _processing_error(ERROR_EMBEDDING_DIMENSION_MISMATCH, retryable=False)
    return vectors
