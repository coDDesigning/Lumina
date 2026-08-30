"""In-process embedding generation.

Embeddings are computed locally and are independent of which vendor answers a
generation request: the same vectors are produced whether this deployment talks
to Gemini, to Ollama, or to nothing. Nothing leaves the machine and there is no
endpoint to be unavailable, so the retryable half of the error taxonomy below
is unreachable here; it is kept because services/document_embedding.py
classifies on it and the job state machine consumes that classification.
"""

import math
import os
import socket
import threading
import urllib.parse
from collections.abc import Sequence
from typing import Protocol

from backend.app.config import EMBEDDING_PROVIDER_LOCAL, settings
from backend.app.embedding_models import EMBEDDING_MODEL


def resolve_ollama_base_url(url_str: str | None = None) -> str:
    """Parse OLLAMA_BASE_URL, defaulting to http://127.0.0.1:11434 if invalid or unresolved.

    Embeddings do not call Ollama; this lives here because text generation and
    image understanding already import it from this module.
    """
    default_url = "http://127.0.0.1:11434"
    raw = (
        url_str
        if url_str is not None
        else (
            getattr(settings, "ollama_base_url", None) or os.getenv("OLLAMA_BASE_URL")
        )
    )
    if not raw or not isinstance(raw, str) or not raw.strip():
        return default_url
    cleaned = raw.strip().rstrip("/")
    try:
        parsed = urllib.parse.urlsplit(cleaned)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return default_url
        hostname = parsed.hostname
        if hostname in {"host.docker.internal", "localhost"}:
            try:
                socket.getaddrinfo(
                    hostname,
                    parsed.port or 11434,
                    socket.AF_UNSPEC,
                    socket.SOCK_STREAM,
                )
            except (socket.gaierror, OSError):
                return default_url
        return cleaned
    except Exception:
        return default_url


class EmbeddingProvider(Protocol):
    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class EmbeddingError(RuntimeError):
    """Base class for every embedding failure surfaced to the pipeline."""

    retryable = False


class EmbeddingTimeoutError(EmbeddingError):
    retryable = True

    def __init__(self, message: str = "Embedding generation timed out.") -> None:
        super().__init__(message)


class EmbeddingConnectionError(EmbeddingError):
    retryable = True

    def __init__(
        self, message: str = "The embedding provider could not be reached."
    ) -> None:
        super().__init__(message)


class EmbeddingRateLimitError(EmbeddingError):
    retryable = True

    def __init__(self, message: str = "Embedding rate limit exceeded.") -> None:
        super().__init__(message)


class EmbeddingProviderError(EmbeddingError):
    retryable = True

    def __init__(self, message: str = "The embedding provider failed.") -> None:
        super().__init__(message)


class EmbeddingInvalidResponseError(EmbeddingError):
    """The provider answered, but not with a usable vector."""

    retryable = False

    def __init__(
        self, message: str = "The embedding provider returned an unusable response."
    ) -> None:
        super().__init__(message)


class EmbeddingDimensionMismatchError(EmbeddingError):
    """The configured model does not match the width the schema stores."""

    retryable = False

    def __init__(self, message: str = "The embedding width is not supported.") -> None:
        super().__init__(message)


class EmbeddingAuthError(EmbeddingError):
    retryable = False

    def __init__(
        self, message: str = "Embedding provider authentication failed."
    ) -> None:
        super().__init__(message)


class EmbeddingConfigurationError(EmbeddingError):
    retryable = False

    def __init__(
        self, message: str = "The embedding configuration is not usable."
    ) -> None:
        super().__init__(message)


def is_transient_embedding_error(exc: Exception) -> bool:
    """Report whether retrying the same request could plausibly succeed."""
    return isinstance(exc, EmbeddingError) and exc.retryable


def _validate_vectors(
    vectors: object,
    *,
    expected_count: int,
) -> list[list[float]]:
    """Reject anything that is not exactly the vectors we asked for.

    Storage is downstream of this check, so a malformed vector must never
    reach the vector store where it would corrupt similarity search.
    """
    if not isinstance(vectors, list):
        raise EmbeddingInvalidResponseError(
            "The embedding provider returned an unexpected response structure."
        )
    if len(vectors) != expected_count:
        raise EmbeddingInvalidResponseError(
            "The embedding provider returned a vector count that does not match "
            "the number of inputs."
        )

    validated: list[list[float]] = []
    for vector in vectors:
        if not isinstance(vector, (list, tuple)) or not vector:
            raise EmbeddingInvalidResponseError(
                "The embedding provider returned an empty vector."
            )
        if len(vector) != EMBEDDING_MODEL.dimensions:
            raise EmbeddingDimensionMismatchError(
                f"Embeddings must contain {EMBEDDING_MODEL.dimensions} values, "
                f"got {len(vector)}."
            )
        values: list[float] = []
        for value in vector:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise EmbeddingInvalidResponseError(
                    "The embedding provider returned a non-numeric vector value."
                )
            number = float(value)
            if not math.isfinite(number):
                raise EmbeddingInvalidResponseError(
                    "The embedding provider returned a vector value that is not finite."
                )
            values.append(number)
        validated.append(values)
    return validated


_shared_model: object | None = None
_shared_model_lock = threading.Lock()
_compute_lock = threading.Lock()


def load_shared_model() -> object:
    """Load the ONNX graph once per process.

    One instance, not one per caller: a multi-gigabyte graph per worker slot
    would cost more memory than the container has, and onnxruntime already
    saturates the available cores from a single session.
    """
    global _shared_model
    with _shared_model_lock:
        if _shared_model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as exc:
                raise EmbeddingConfigurationError(
                    "fastembed is not installed; install requirements.txt."
                ) from exc

            try:
                _shared_model = TextEmbedding(
                    model_name=EMBEDDING_MODEL.model_id,
                    cache_dir=settings.embedding_model_cache_directory,
                    local_files_only=True,
                )
            except Exception as exc:
                raise EmbeddingConfigurationError(
                    f"The embedding model '{EMBEDDING_MODEL.model_id}' is not "
                    f"present in "
                    f"'{settings.embedding_model_cache_directory}'. Container "
                    "images bake it at build time; a checkout downloads it once "
                    "with `python scripts/fetch_embedding_model.py`."
                ) from exc
        return _shared_model


class LocalEmbeddingProvider:
    """fastembed ONNX embeddings computed in this process.

    The query and passage prefixes are applied here rather than left to the
    library, because the library treats them as optional and a silently
    unprefixed query is a recall loss no test would notice.
    """

    PROVIDER_NAME = EMBEDDING_PROVIDER_LOCAL

    def __init__(self, *, model: object | None = None) -> None:
        self._model = model

    def _embed(self, texts: list[str]) -> list[list[float]]:
        model = self._model if self._model is not None else load_shared_model()
        with _compute_lock:
            try:
                raw = list(model.embed(texts, batch_size=settings.embedding_batch_size))
            except EmbeddingError:
                raise
            except Exception as exc:
                raise EmbeddingProviderError(
                    "Local embedding generation failed."
                ) from exc
        return _validate_vectors(
            [list(vector) for vector in raw], expected_count=len(texts)
        )

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        prefix = EMBEDDING_MODEL.passage_prefix
        return self._embed([f"{prefix}{text}" for text in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._embed([f"{EMBEDDING_MODEL.query_prefix}{text}"])[0]


def configured_embedding_identity() -> tuple[str, str]:
    """Report the provider and model that vectors should be attributed to."""
    return EMBEDDING_PROVIDER_LOCAL, EMBEDDING_MODEL.model_id


def get_embedding_provider() -> EmbeddingProvider:
    return LocalEmbeddingProvider()
