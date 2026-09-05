"""The AI artifacts a course has already generated.

Feature services own generation; this module owns the ``generated_outputs``
row. Writing goes through ``record`` so every feature stores attribution and its
settings the same way. Persisted JSON is parsed permissively on the way out: a
row whose stored document no longer matches its feature schema must still
render, because a history view that fails on one bad row is worse than one that
shows it.
"""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import GeneratedOutput
from schemas.generated_output import GeneratedOutputDetail, GeneratedOutputSummary
from utils.exceptions import NotFoundException
from utils.json_documents import parse_json_object

GENERATED_OUTPUT_NOT_FOUND = "Generated output not found"


def _parse_object(
    raw: str | None, *, field: str, output_id: int
) -> dict[str, Any] | None:
    return parse_json_object(
        raw, field=field, table="generated_outputs", row_id=output_id
    )


def _parse_content(row: GeneratedOutput) -> dict[str, Any] | str:
    parsed = _parse_object(row.content, field="content", output_id=row.id)
    return row.content if parsed is None else parsed


class GeneratedOutputService:
    @staticmethod
    def record(
        db: Session,
        *,
        course_id: int,
        user_id: int,
        output_type: str,
        content: str,
        model_used: str | None = None,
        generation_settings: str | None = None,
        generation_context: str | None = None,
        commit: bool = True,
    ) -> GeneratedOutput:
        """Store one generation, with the user and model that actually produced it.

        ``commit=False`` hands the transaction back to a caller that must write
        rows referencing the new identifier, so the parent row and its children
        succeed or fail together. ``QuizService.save_generated_quiz`` already
        does the same in reverse, staging its rows and letting this function's
        commit cover both. Either way this stays the only place a
        ``GeneratedOutput`` is constructed.
        """
        generated_output = GeneratedOutput(
            course_id=course_id,
            user_id=user_id,
            model_used=model_used,
            output_type=output_type,
            content=content,
            generation_settings=generation_settings,
            generation_context=generation_context,
        )
        db.add(generated_output)
        db.flush()
        db.refresh(generated_output)
        if commit:
            db.commit()

        return generated_output

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
    def find_quiz_output(
        db: Session,
        *,
        course_id: int,
        user_id: int,
        output_type: str,
        quiz_id: int,
    ) -> GeneratedOutput | None:
        """Find the history row atomically persisted beside one Exam Mode quiz."""
        outputs = db.scalars(
            select(GeneratedOutput)
            .where(
                GeneratedOutput.course_id == course_id,
                GeneratedOutput.user_id == user_id,
                GeneratedOutput.output_type == output_type,
            )
            .order_by(GeneratedOutput.created_at.desc(), GeneratedOutput.id.desc())
        ).all()
        for output in outputs:
            content = _parse_object(
                output.content, field="content", output_id=output.id
            )
            if content is not None and content.get("quiz_id") == quiz_id:
                return output
        return None

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
