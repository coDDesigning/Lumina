"""Reading versioned JSON documents back out of text columns.

Feature services write these strictly, through their Pydantic models. Reading
them back is deliberately permissive: a single row whose stored document no
longer matches its schema must still render, because a history view that fails
on one bad row is worse than one that shows it.
"""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def parse_json_object(
    raw: str | None, *, field: str, table: str, row_id: int
) -> dict[str, Any] | None:
    """Parse a stored JSON object, or log and return ``None`` if it is unusable."""
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        logger.warning("%s.%s for row %s is not valid JSON", table, field, row_id)
        return None
    if not isinstance(parsed, dict):
        logger.warning("%s.%s for row %s is not a JSON object", table, field, row_id)
        return None
    return parsed
