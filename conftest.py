import os

# A vendor is available only when its credential is configured, so the suite
# must name one before backend.app.config is imported.
os.environ.setdefault("OLLAMA_BASE_URL", "http://localhost:11434")
