"""Reaching the Ollama server.

Shared by text generation and image understanding. Embeddings are computed
in-process and never call Ollama, which is why this does not live beside them.
"""

import os
import socket
import urllib.parse

from backend.app.config import settings


def resolve_ollama_base_url(url_str: str | None = None) -> str:
    """Parse OLLAMA_BASE_URL, defaulting to http://127.0.0.1:11434 if invalid or unresolved."""
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
