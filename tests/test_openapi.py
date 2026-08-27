"""Test ensuring FastAPI OpenAPI schema matches the committed snapshot in docs/openapi.json."""

import json
from pathlib import Path

from main import app

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OPENAPI_SNAPSHOT_PATH = PROJECT_ROOT / "docs" / "openapi.json"


def test_openapi_matches_committed_snapshot() -> None:
    assert OPENAPI_SNAPSHOT_PATH.is_file(), (
        f"Committed OpenAPI snapshot not found at {OPENAPI_SNAPSHOT_PATH}. "
        "Run 'python scripts/export_openapi.py' to generate it."
    )

    expected_content = OPENAPI_SNAPSHOT_PATH.read_text(encoding="utf-8")
    actual_schema = app.openapi()
    actual_content = json.dumps(actual_schema, indent=2, sort_keys=True) + "\n"

    assert actual_content == expected_content, (
        "OpenAPI schema has drifted from the committed snapshot at docs/openapi.json.\n"
        "If you intentionally changed backend endpoints, parameters, or schemas, update the snapshot by running:\n"
        "    python scripts/export_openapi.py\n"
        "Then review the diff in docs/openapi.json before committing."
    )
