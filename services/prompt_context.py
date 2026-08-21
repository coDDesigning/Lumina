from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import UploadedDocument, User
from schemas.prompt_context import (
    MAX_COURSE_TITLE_CHARS,
    MAX_SUBJECT_AREA_CHARS,
    UNSPECIFIED_COURSE_TITLE,
    UNSPECIFIED_SUBJECT_AREA,
    EducationLevel,
    MaterialKind,
    PromptContext,
)

__all__ = ["resolve_prompt_context"]


def _coerce_education_level(value: Any) -> EducationLevel:
    if isinstance(value, EducationLevel):
        return value
    if not isinstance(value, str):
        return EducationLevel.UNSPECIFIED
    try:
        return EducationLevel(value.strip())
    except ValueError:
        return EducationLevel.UNSPECIFIED


def _coerce_material_kind(value: Any) -> MaterialKind:
    if isinstance(value, MaterialKind):
        return value
    if not isinstance(value, str):
        return MaterialKind.UNSPECIFIED
    try:
        return MaterialKind(value.strip())
    except ValueError:
        return MaterialKind.UNSPECIFIED


def _clean_text(value: Any, fallback: str, max_chars: int) -> str:
    if not isinstance(value, str):
        return fallback
    cleaned = value.replace("\x00", "").replace("{", "").replace("}", "").strip()
    if not cleaned:
        return fallback
    return cleaned[:max_chars]


def _resolve_education_level(
    db: Session, course: Any, user_id: int | None
) -> EducationLevel:
    course_level = _coerce_education_level(getattr(course, "education_level", None))
    if course_level is not EducationLevel.UNSPECIFIED:
        return course_level

    owner_id = user_id if user_id is not None else getattr(course, "owner_id", None)
    if owner_id is None:
        return EducationLevel.UNSPECIFIED

    user = db.get(User, owner_id)
    if user is None:
        return EducationLevel.UNSPECIFIED
    return _coerce_education_level(user.education_level)


def _resolve_material_kind(
    db: Session, course_id: int | None, document_ids: Sequence[UUID] | None
) -> MaterialKind:
    statement = select(UploadedDocument.material_kind).distinct()
    if document_ids is not None:
        if not document_ids:
            return MaterialKind.UNSPECIFIED
        statement = statement.where(UploadedDocument.id.in_(list(document_ids)))
    elif course_id is not None:
        statement = statement.where(
            UploadedDocument.course_id == course_id,
            UploadedDocument.status == "ready",
        )
    else:
        return MaterialKind.UNSPECIFIED

    kinds = {_coerce_material_kind(row) for row in db.scalars(statement).all()} - {
        MaterialKind.UNSPECIFIED
    }

    if not kinds:
        return MaterialKind.UNSPECIFIED
    if len(kinds) == 1:
        return next(iter(kinds))
    return MaterialKind.MIXED


def resolve_prompt_context(
    db: Session,
    *,
    course: Any | None = None,
    user_id: int | None = None,
    document_ids: Sequence[UUID] | None = None,
) -> PromptContext:
    return PromptContext(
        education_level=_resolve_education_level(db, course, user_id),
        course_title=_clean_text(
            getattr(course, "title", None),
            UNSPECIFIED_COURSE_TITLE,
            MAX_COURSE_TITLE_CHARS,
        ),
        subject_area=_clean_text(
            getattr(course, "subject_area", None),
            UNSPECIFIED_SUBJECT_AREA,
            MAX_SUBJECT_AREA_CHARS,
        ),
        material_kind=_resolve_material_kind(
            db, getattr(course, "id", None), document_ids
        ),
    )
