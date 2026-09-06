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
import threading
from collections.abc import Sequence
from typing import Protocol

import httpx

from backend.app.config import EMBEDDING_PROVIDER_LOCAL, settings
from backend.app.embedding_models import EMBEDDING_MODEL


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


_shared_http_client: httpx.Client | None = None
_shared_http_client_lock = threading.Lock()


def _get_shared_http_client() -> httpx.Client:
    global _shared_http_client
    with _shared_http_client_lock:
        if _shared_http_client is None:
            _shared_http_client = httpx.Client()
        return _shared_http_client


class OllamaEmbeddingProvider:
    PROVIDER_NAME = "ollama"

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        client: httpx.Client | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        self._base_url = (
            base_url
            or os.getenv("OLLAMA_BASE_URL", "").strip()
            or getattr(settings, "ollama_base_url", None)
            or "http://127.0.0.1:11434"
        ).rstrip("/")
        self._model = (
            model
            or os.getenv("EMBEDDING_MODEL", "").strip()
            or os.getenv("OLLAMA_EMBEDDING_MODEL", "").strip()
            or "nomic-embed-text"
        )
        self._batch_size = getattr(settings, "embedding_batch_size", 32)
        self._timeout_seconds = timeout_seconds or 60
        self._client = client or _get_shared_http_client()

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = self._client.post(
                f"{self._base_url}/api/embed",
                json={"model": self._model, "input": texts, "truncate": True},
                timeout=self._timeout_seconds,
            )
            if response.status_code == 404:
                raw_vectors = []
                for text in texts:
                    res = self._client.post(
                        f"{self._base_url}/api/embeddings",
                        json={"model": self._model, "prompt": text},
                        timeout=self._timeout_seconds,
                    )
                    if not res.is_success:
                        raise EmbeddingProviderError(
                            f"Ollama returned HTTP {res.status_code}: {res.text}"
                        )
                    raw_vectors.append(res.json().get("embedding", []))
            elif response.status_code == 429:
                raise EmbeddingRateLimitError("Ollama rate limit exceeded.")
            elif not response.is_success:
                raise EmbeddingProviderError(
                    f"Ollama returned HTTP {response.status_code}: {response.text}"
                )
            else:
                envelope = response.json()
                raw_vectors = envelope.get("embeddings", [])
        except httpx.TimeoutException as exc:
            raise EmbeddingTimeoutError(
                "Ollama did not return embeddings within the configured timeout."
            ) from exc
        except httpx.TransportError as exc:
            raise EmbeddingConnectionError(
                f"Ollama could not be reached at {self._base_url}."
            ) from exc
        except Exception as exc:
            if isinstance(exc, EmbeddingError):
                raise
            raise EmbeddingProviderError(f"Ollama embedding failed: {exc}") from exc

        target_dim = EMBEDDING_MODEL.dimensions
        adjusted_vectors = []
        for vec in raw_vectors:
            if isinstance(vec, (list, tuple)):
                v = [float(x) for x in vec]
                if len(v) < target_dim:
                    v = v + [0.0] * (target_dim - len(v))
                elif len(v) > target_dim:
                    v = v[:target_dim]
                adjusted_vectors.append(v)
            else:
                adjusted_vectors.append(vec)

        return _validate_vectors(adjusted_vectors, expected_count=len(texts))

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), self._batch_size):
            batch = list(texts[i : i + self._batch_size])
            vectors.extend(self._embed_batch(batch))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._embed_batch([text])[0]


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
    """fastembed ONNX embeddings computed in this process with graceful Ollama fallback.

    The query and passage prefixes are applied here rather than left to the
    library, because the library treats them as optional and a silently
    unprefixed query is a recall loss no test would notice.
    """

    PROVIDER_NAME = EMBEDDING_PROVIDER_LOCAL

    def __init__(self, *, model: object | None = None) -> None:
        self._model = model

    def _embed(self, texts: list[str]) -> list[list[float]]:
        try:
            model = self._model if self._model is not None else load_shared_model()
        except EmbeddingConfigurationError:
            return OllamaEmbeddingProvider().embed_documents(texts)

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
    provider_name = (
        os.getenv("EMBEDDING_PROVIDER", "").strip().lower()
        or getattr(settings, "embedding_provider", None)
        or EMBEDDING_PROVIDER_LOCAL
    )
    if provider_name == "ollama":
        model_name = (
            os.getenv("EMBEDDING_MODEL", "").strip()
            or os.getenv("OLLAMA_EMBEDDING_MODEL", "").strip()
            or "nomic-embed-text"
        )
        return "ollama", model_name
    return EMBEDDING_PROVIDER_LOCAL, EMBEDDING_MODEL.model_id


def get_embedding_provider() -> EmbeddingProvider:
    provider_name = (
        os.getenv("EMBEDDING_PROVIDER", "").strip().lower()
        or getattr(settings, "embedding_provider", None)
        or EMBEDDING_PROVIDER_LOCAL
    )
    if provider_name == "ollama":
        return OllamaEmbeddingProvider()
    return LocalEmbeddingProvider()
