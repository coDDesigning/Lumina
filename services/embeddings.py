"""Embedding generation providers and their configuration-driven factory.

Embeddings are a separate seam from text generation: a deployment may pair a
large generative model with a small dedicated embedding model, and the two
call different endpoints with different response contracts. Feature code asks
for vectors and never learns which provider produced them.
"""

import math
import os
import socket
import urllib.parse
from collections.abc import Iterator, Sequence
from typing import Protocol

import httpx
from google import genai
from google.genai import types

from backend.app.config import (
    AI_PROVIDER_GEMINI,
    AI_PROVIDER_OLLAMA,
    IMPLEMENTED_EMBEDDING_PROVIDERS,
    settings,
)
from backend.app.models import EMBEDDING_DIMENSIONS


def resolve_ollama_base_url(url_str: str | None = None) -> str:
    """Parse OLLAMA_BASE_URL, defaulting to http://127.0.0.1:11434 if invalid or unresolved."""
    default_url = "http://127.0.0.1:11434"
    raw = (
        url_str
        if url_str is not None
        else (getattr(settings, "ollama_base_url", None) or os.getenv("OLLAMA_BASE_URL"))
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


def _batched(texts: Sequence[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(texts), size):
        yield list(texts[start : start + size])


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
        if len(vector) != EMBEDDING_DIMENSIONS:
            raise EmbeddingDimensionMismatchError(
                f"Embeddings must contain {EMBEDDING_DIMENSIONS} values, "
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


def _get_shared_http_client() -> httpx.Client:
    global _shared_http_client
    if _shared_http_client is None:
        _shared_http_client = httpx.Client()
    return _shared_http_client


class OllamaEmbeddingProvider:
    PROVIDER_NAME = AI_PROVIDER_OLLAMA
    EMBED_PATH = "/api/embed"

    def __init__(
        self,
        client: httpx.Client | None = None,
        timeout_seconds: int | None = None,
        base_url: str | None = None,
    ) -> None:
        self._base_url = resolve_ollama_base_url(base_url or settings.ollama_base_url)
        self._model = settings.ollama_embedding_model
        self._batch_size = settings.embedding_batch_size
        self._timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else settings.embedding_timeout_seconds
        )
        self._client = client or _get_shared_http_client()

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        try:
            response = self._client.post(
                f"{self._base_url}{self.EMBED_PATH}",
                json={"model": self._model, "input": texts, "truncate": True},
                timeout=self._timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise EmbeddingTimeoutError(
                "Ollama did not return embeddings within the configured timeout."
            ) from exc
        except httpx.TransportError as exc:
            if self._base_url != "http://127.0.0.1:11434":
                try:
                    fallback_url = "http://127.0.0.1:11434"
                    response = self._client.post(
                        f"{fallback_url}{self.EMBED_PATH}",
                        json={"model": self._model, "input": texts, "truncate": True},
                        timeout=self._timeout_seconds,
                    )
                    self._base_url = fallback_url
                except httpx.TimeoutException as timeout_exc:
                    raise EmbeddingTimeoutError(
                        "Ollama did not return embeddings within the configured timeout."
                    ) from timeout_exc
                except httpx.TransportError as transport_exc:
                    raise EmbeddingConnectionError(
                        "Ollama could not be reached at the configured base URL."
                    ) from transport_exc
            else:
                raise EmbeddingConnectionError(
                    "Ollama could not be reached at the configured base URL."
                ) from exc

        if response.status_code == 429:
            raise EmbeddingRateLimitError("Ollama rate limit exceeded.")
        if not response.is_success:
            raise EmbeddingProviderError(
                f"Ollama returned HTTP {response.status_code}."
            )

        try:
            envelope = response.json()
        except ValueError as exc:
            raise EmbeddingInvalidResponseError(
                "Ollama returned a response that is not valid JSON."
            ) from exc
        if not isinstance(envelope, dict):
            raise EmbeddingInvalidResponseError(
                "Ollama returned an unexpected response structure."
            )

        return _validate_vectors(
            envelope.get("embeddings"),
            expected_count=len(texts),
        )

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for batch in _batched(texts, self._batch_size):
            vectors.extend(self._embed_batch(batch))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._embed_batch([text])[0]


class GeminiEmbeddingProvider:
    PROVIDER_NAME = AI_PROVIDER_GEMINI

    def __init__(
        self,
        client: object | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        api_key = settings.gemini_api_key
        if client is None and not api_key:
            raise EmbeddingConfigurationError(
                "EMBEDDING_PROVIDER 'gemini' requires GEMINI_API_KEY to be set."
            )
        self._model = settings.gemini_embedding_model
        self._batch_size = settings.embedding_batch_size
        timeout_sec = (
            timeout_seconds
            if timeout_seconds is not None
            else settings.embedding_timeout_seconds
        )
        http_options = types.HttpOptions(timeout=int(timeout_sec * 1000))
        self._client = client or genai.Client(
            api_key=api_key,
            http_options=http_options,
        )

    def _handle_client_error(self, exc: Exception) -> None:
        if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
            raise EmbeddingTimeoutError("Gemini embedding request timed out.") from exc
        status = getattr(exc, "code", None)
        if status == 429:
            raise EmbeddingRateLimitError("Gemini rate limit exceeded.") from exc
        if status in {401, 403}:
            raise EmbeddingAuthError(
                "Gemini rejected the configured credentials."
            ) from exc
        if isinstance(status, int) and 400 <= status < 500:
            raise EmbeddingInvalidResponseError(
                "Gemini rejected the embedding request."
            ) from exc
        raise EmbeddingProviderError("Gemini failed to return embeddings.") from exc

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        try:
            response = self._client.models.embed_content(
                model=self._model,
                contents=texts,
                config={"output_dimensionality": EMBEDDING_DIMENSIONS},
            )
        except Exception as exc:
            self._handle_client_error(exc)

        raw = getattr(response, "embeddings", None)
        if not isinstance(raw, list):
            raise EmbeddingInvalidResponseError(
                "Gemini returned an unexpected response structure."
            )
        return _validate_vectors(
            [getattr(item, "values", None) for item in raw],
            expected_count=len(texts),
        )

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for batch in _batched(texts, self._batch_size):
            vectors.extend(self._embed_batch(batch))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._embed_batch([text])[0]


def configured_embedding_identity() -> tuple[str, str]:
    """Report the provider and model that vectors should be attributed to."""
    if settings.embedding_provider == AI_PROVIDER_GEMINI:
        return AI_PROVIDER_GEMINI, settings.gemini_embedding_model
    return AI_PROVIDER_OLLAMA, settings.ollama_embedding_model


def get_embedding_provider() -> EmbeddingProvider:
    provider_name = settings.embedding_provider
    if provider_name not in IMPLEMENTED_EMBEDDING_PROVIDERS:
        raise EmbeddingConfigurationError(
            f"Embedding provider '{provider_name}' is not implemented."
        )
    if provider_name == AI_PROVIDER_GEMINI:
        return GeminiEmbeddingProvider()
    return OllamaEmbeddingProvider()
