"""Export deterministic OpenAPI JSON schema from FastAPI main app."""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OPENAPI_SNAPSHOT_PATH = PROJECT_ROOT / "docs" / "openapi.json"


def export_openapi(target_path: Path = OPENAPI_SNAPSHOT_PATH) -> str:
    """Generate deterministic sorted OpenAPI JSON and write to target_path."""
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from main import app

    schema = app.openapi()
    content = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(content, encoding="utf-8")
    return content


if __name__ == "__main__":
    export_openapi()
    print(
        f"Exported OpenAPI snapshot to {OPENAPI_SNAPSHOT_PATH.relative_to(PROJECT_ROOT)}"
    )
