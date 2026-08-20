"""Reading the AI artifacts a course has already generated.

Rows are written by the feature services; this module is the only place that
reads them back. Persisted JSON is parsed permissively on the way out: a row
whose stored document no longer matches its feature schema must still render,
because a history view that fails on one bad row is worse than one that shows it.
"""

import json
import logging
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import GeneratedOutput
from schemas.generated_output import GeneratedOutputDetail, GeneratedOutputSummary
from utils.exceptions import NotFoundException

logger = logging.getLogger(__name__)

GENERATED_OUTPUT_NOT_FOUND = "Generated output not found"


def _parse_object(
    raw: str | None, *, field: str, output_id: int
) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        logger.warning(
            "generated_outputs.%s for row %s is not valid JSON", field, output_id
        )
        return None
    if not isinstance(parsed, dict):
        logger.warning(
            "generated_outputs.%s for row %s is not a JSON object", field, output_id
        )
        return None
    return parsed


def _parse_content(row: GeneratedOutput) -> dict[str, Any] | str:
    parsed = _parse_object(row.content, field="content", output_id=row.id)
    return row.content if parsed is None else parsed


class GeneratedOutputService:
    @staticmethod
    def list_course_outputs(
        db: Session,
        course_id: int,
    ) -> Sequence[GeneratedOutput]:
        """List the generated outputs of an already authorized course, newest first."""
        return db.scalars(
            select(GeneratedOutput)
            .where(GeneratedOutput.course_id == course_id)
            .order_by(GeneratedOutput.created_at.desc(), GeneratedOutput.id.desc())
        ).all()

    @staticmethod
    def get_course_output(
        db: Session,
        course_id: int,
        output_id: int,
    ) -> GeneratedOutput:
        """Load one output scoped to its parent course, or deny without disclosure.

        The course predicate lives in the same statement as the identifier, so an
        output belonging to another course is indistinguishable from one that does
        not exist.
        """
        output = db.scalar(
            select(GeneratedOutput).where(
                GeneratedOutput.id == output_id,
                GeneratedOutput.course_id == course_id,
            )
        )
        if output is None:
            raise NotFoundException(detail=GENERATED_OUTPUT_NOT_FOUND)
        return output

    @staticmethod
    def summarize(row: GeneratedOutput) -> GeneratedOutputSummary:
        return GeneratedOutputSummary(
            id=row.id,
            course_id=row.course_id,
            output_type=row.output_type,
            user_id=row.user_id,
            model_used=row.model_used,
            created_at=row.created_at,
            generation_settings=_parse_object(
                row.generation_settings,
                field="generation_settings",
                output_id=row.id,
            ),
            generation_context=_parse_object(
                row.generation_context,
                field="generation_context",
                output_id=row.id,
            ),
        )

    @classmethod
    def detail(cls, row: GeneratedOutput) -> GeneratedOutputDetail:
        return GeneratedOutputDetail(
            **cls.summarize(row).model_dump(),
            content=_parse_content(row),
        )
