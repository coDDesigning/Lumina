from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.models import ProfileKnowledge
from schemas.profile_knowledge import (
    ProfileKnowledgeCreate,
    ProfileKnowledgeImport,
    ProfileKnowledgeUpdate,
)
from services.course_material import CourseMaterial, load_course_material

ITEM_SEPARATOR = "\n\n"
DEFAULT_PROFILE_KNOWLEDGE_BUDGET = 2000


@dataclass(frozen=True)
class ProfileKnowledgeContext:
    """Loaded profile knowledge text for AI context injection."""

    text: str
    items_used: int
    items_available: int
    truncated: bool

    @property
    def is_empty(self) -> bool:
        return not self.text


EMPTY_PROFILE_KNOWLEDGE = ProfileKnowledgeContext(
    text="", items_used=0, items_available=0, truncated=False
)


@dataclass(frozen=True)
class GenerationContext:
    """Assembled AI context enforcing course-primary, profile-supplementary rules."""

    course_material: CourseMaterial
    profile_knowledge: ProfileKnowledgeContext

    @property
    def is_empty(self) -> bool:
        return self.course_material.is_empty


PROFILE_CONTEXT_HEADER = (
    "==================================================\n"
    "SUPPLEMENTARY PROFILE CONTEXT\n"
    "=================================================="
)

PROFILE_CONTEXT_DIRECTIVE = (
    "The following background knowledge is student-provided supplementary context. "
    "Course material is primary and authoritative; profile context must NEVER override "
    "or contradict course material."
)


def format_profile_context(context: ProfileKnowledgeContext | None) -> str:
    """Format profile knowledge into a clearly delimited supplementary prompt block.

    Returns an empty string when profile knowledge is absent or empty.
    """
    if context is None or context.is_empty:
        return ""
    return f"{PROFILE_CONTEXT_HEADER}\n\n{PROFILE_CONTEXT_DIRECTIVE}\n\n{context.text}"


class ProfileKnowledgeService:
    """Manages student-owned profile knowledge entries and retrieval assembly."""

    @staticmethod
    def create(
        db: Session,
        user_id: int,
        payload: ProfileKnowledgeCreate,
    ) -> ProfileKnowledge:
        entry = ProfileKnowledge(
            user_id=user_id,
            topic=payload.topic,
            detail=payload.detail,
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return entry

    @staticmethod
    def get_by_id(
        db: Session,
        user_id: int,
        item_id: int,
    ) -> ProfileKnowledge | None:
        statement = select(ProfileKnowledge).where(
            ProfileKnowledge.id == item_id,
            ProfileKnowledge.user_id == user_id,
        )
        return db.scalar(statement)

    @staticmethod
    def list_by_user(
        db: Session,
        user_id: int,
    ) -> list[ProfileKnowledge]:
        statement = (
            select(ProfileKnowledge)
            .where(ProfileKnowledge.user_id == user_id)
            .order_by(ProfileKnowledge.created_at.desc(), ProfileKnowledge.id.desc())
        )
        return list(db.scalars(statement).all())

    @staticmethod
    def update(
        db: Session,
        user_id: int,
        item_id: int,
        payload: ProfileKnowledgeUpdate,
    ) -> ProfileKnowledge | None:
        entry = ProfileKnowledgeService.get_by_id(db, user_id, item_id)
        if entry is None:
            return None

        if payload.topic is not None:
            entry.topic = payload.topic
        if payload.detail is not None:
            entry.detail = payload.detail

        db.commit()
        db.refresh(entry)
        return entry

    @staticmethod
    def delete(
        db: Session,
        user_id: int,
        item_id: int,
    ) -> bool:
        entry = ProfileKnowledgeService.get_by_id(db, user_id, item_id)
        if entry is None:
            return False

        db.execute(
            delete(ProfileKnowledge).where(
                ProfileKnowledge.id == item_id,
                ProfileKnowledge.user_id == user_id,
            )
        )
        db.commit()
        return True

    @staticmethod
    def delete_all(
        db: Session,
        user_id: int,
    ) -> int:
        result = db.execute(
            delete(ProfileKnowledge).where(ProfileKnowledge.user_id == user_id)
        )
        db.commit()
        return result.rowcount

    @staticmethod
    def bulk_import(
        db: Session,
        user_id: int,
        payload: ProfileKnowledgeImport,
    ) -> list[ProfileKnowledge]:
        created_entries: list[ProfileKnowledge] = []
        for item in payload.items:
            entry = ProfileKnowledge(
                user_id=user_id,
                topic=item.topic,
                detail=item.detail,
            )
            db.add(entry)
            created_entries.append(entry)

        db.commit()
        for entry in created_entries:
            db.refresh(entry)
        return created_entries


def load_profile_knowledge(
    db: Session,
    user_id: int,
    *,
    max_characters: int = DEFAULT_PROFILE_KNOWLEDGE_BUDGET,
) -> ProfileKnowledgeContext:
    """Loads student profile knowledge formatted for AI context within budget bounds."""
    if max_characters <= 0:
        raise ValueError("max_characters must be a positive integer.")

    statement = (
        select(ProfileKnowledge.topic, ProfileKnowledge.detail)
        .where(ProfileKnowledge.user_id == user_id)
        .order_by(ProfileKnowledge.created_at.asc(), ProfileKnowledge.id.asc())
    )

    eligible: list[str] = []
    for topic, detail in db.execute(statement).all():
        topic_str = (topic or "").strip()
        detail_str = (detail or "").strip()
        if not topic_str and not detail_str:
            continue
        eligible.append(f"Topic: {topic_str}\nDetail: {detail_str}")

    parts: list[str] = []
    length = 0
    truncated = False

    for formatted in eligible:
        addition = len(formatted) + (len(ITEM_SEPARATOR) if parts else 0)
        if length + addition > max_characters:
            truncated = True
            break
        parts.append(formatted)
        length += addition

    return ProfileKnowledgeContext(
        text=ITEM_SEPARATOR.join(parts),
        items_used=len(parts),
        items_available=len(eligible),
        truncated=truncated,
    )


def load_profile_knowledge_for_generation(
    db: Session,
    user_id: int | None,
    *,
    opted_in: bool,
    max_characters: int = DEFAULT_PROFILE_KNOWLEDGE_BUDGET,
) -> ProfileKnowledgeContext:
    """Single consent gate: profile knowledge is queried only for an opted-in owner."""
    if user_id is None or not opted_in:
        return EMPTY_PROFILE_KNOWLEDGE
    return load_profile_knowledge(db, user_id, max_characters=max_characters)


def assemble_generation_context(
    db: Session,
    course_id: int,
    user_id: int | None,
    *,
    course_material: CourseMaterial | None = None,
    course_max_characters: int | None = None,
    profile_max_characters: int = DEFAULT_PROFILE_KNOWLEDGE_BUDGET,
    include_profile_context: bool = False,
    use_profile_knowledge: bool | None = None,
) -> GenerationContext:
    """Assembles course material and profile knowledge under strict priority rules.

    Course material is primary and authoritative. Profile knowledge is supplementary
    context isolated strictly to the requesting user and queried only when
    opted in (defaults to False).
    """
    if use_profile_knowledge is not None:
        include_profile_context = use_profile_knowledge

    if course_material is None:
        if course_max_characters is None:
            raise ValueError(
                "Either course_material or course_max_characters must be provided."
            )
        course_material = load_course_material(
            db,
            course_id,
            max_characters=course_max_characters,
        )

    profile_context = load_profile_knowledge_for_generation(
        db,
        user_id,
        opted_in=include_profile_context,
        max_characters=profile_max_characters,
    )

    return GenerationContext(
        course_material=course_material,
        profile_knowledge=profile_context,
    )
