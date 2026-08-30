"""Download the embedding model's ONNX weights into the image cache.

A build-time tool, not application configuration: it reads the environment
directly because it runs before the application exists, the same exception
backend/app/database_config.py makes for the Alembic migrator.
"""

import os
import sys

from fastembed import TextEmbedding

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app.embedding_models import EMBEDDING_MODEL  # noqa: E402


def main() -> int:
    cache_directory = os.getenv("EMBEDDING_MODEL_CACHE_DIRECTORY", "").strip()
    if not cache_directory:
        print("EMBEDDING_MODEL_CACHE_DIRECTORY must be set.", file=sys.stderr)
        return 1

    os.makedirs(cache_directory, exist_ok=True)
    model = TextEmbedding(
        model_name=EMBEDDING_MODEL.model_id, cache_dir=cache_directory
    )

    probe = list(model.embed([f"{EMBEDDING_MODEL.passage_prefix}probe"]))
    width = len(probe[0])
    if width != EMBEDDING_MODEL.dimensions:
        print(
            f"{EMBEDDING_MODEL.model_id} produced {width} dimensions, "
            f"expected {EMBEDDING_MODEL.dimensions}.",
            file=sys.stderr,
        )
        return 1

    print(f"{EMBEDDING_MODEL.model_id} cached in {cache_directory} ({width}d).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
