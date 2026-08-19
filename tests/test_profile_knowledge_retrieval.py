"""Retrieval priority and cross-user isolation tests for profile knowledge (SCRUM-78)."""

from uuid import uuid4

from backend.app.models import DocumentChunk, ProfileKnowledge, UploadedDocument
from services.profile_knowledge import (
    assemble_generation_context,
    load_profile_knowledge,
)


def _seed_document_with_chunk(
    session,
    course_id: int,
    user_id: int,
    text: str,
) -> None:
    doc = UploadedDocument(
        id=uuid4(),
        course_id=course_id,
        user_id=user_id,
        original_file_name="lecture.txt",
        file_type="txt",
        mime_type="text/plain",
        file_size=len(text.encode("utf-8")),
        file_hash=uuid4().hex * 2,
        storage_provider="local",
        storage_key=f"courses/{course_id}/docs/{uuid4()}.txt",
        status="ready",
    )
    session.add(doc)
    session.flush()

    chunk = DocumentChunk(
        document_id=doc.id,
        course_id=course_id,
        chunk_index=0,
        text=text,
    )
    session.add(chunk)
    session.commit()


def test_load_profile_knowledge_formats_and_bounds_budget(
    authz_api,
) -> None:
    factory = authz_api.session_factory
    user_id = authz_api.user_a_id

    with factory() as session:
        session.add_all(
            [
                ProfileKnowledge(
                    user_id=user_id,
                    topic="Topic One",
                    detail="Detail for topic one.",
                ),
                ProfileKnowledge(
                    user_id=user_id,
                    topic="Topic Two",
                    detail="Detail for topic two.",
                ),
            ]
        )
        session.commit()

    with factory() as session:
        # 1. Full load
        context = load_profile_knowledge(session, user_id, max_characters=2000)
        assert not context.is_empty
        assert context.items_available == 2
        assert context.items_used == 2
        assert not context.truncated
        assert "Topic: Topic One" in context.text
        assert "Detail: Detail for topic one." in context.text
        assert "Topic: Topic Two" in context.text

        # 2. Bounded / truncated load
        short_context = load_profile_knowledge(session, user_id, max_characters=50)
        assert short_context.items_used == 1
        assert short_context.truncated
        assert "Topic: Topic One" in short_context.text
        assert "Topic: Topic Two" not in short_context.text


def test_assemble_generation_context_course_first_priority(
    authz_api,
) -> None:
    factory = authz_api.session_factory
    user_id = authz_api.user_a_id
    course_id = authz_api.a_course_id

    course_text = (
        "Course Fact: Photons have zero rest mass according to special relativity."
    )
    profile_text = "Student note: Photons might have mass in non-standard physics."

    with factory() as session:
        _seed_document_with_chunk(session, course_id, user_id, course_text)
        session.add(
            ProfileKnowledge(
                user_id=user_id,
                topic="Particle Physics",
                detail=profile_text,
            )
        )
        session.commit()

    with factory() as session:
        context = assemble_generation_context(
            session,
            course_id=course_id,
            user_id=user_id,
            course_max_characters=1000,
            profile_max_characters=1000,
        )

        assert not context.is_empty
        # Course material is present at the beginning (primary)
        assert context.combined_text.startswith(course_text)
        # Profile knowledge is appended as supplementary
        assert "[Supplementary Student Knowledge Profile]" in context.combined_text
        assert "Topic: Particle Physics" in context.combined_text
        assert profile_text in context.combined_text


def test_assemble_generation_context_empty_course_material(
    authz_api,
) -> None:
    factory = authz_api.session_factory
    user_id = authz_api.user_a_id
    # Course with no ready chunks
    course_id = authz_api.b_course_id

    with factory() as session:
        session.add(
            ProfileKnowledge(
                user_id=user_id,
                topic="Standalone Topic",
                detail="Student knows this topic.",
            )
        )
        session.commit()

    with factory() as session:
        context = assemble_generation_context(
            session,
            course_id=course_id,
            user_id=user_id,
            course_max_characters=1000,
        )
        # Without course material, course-scoped generation context is empty
        assert context.is_empty
        assert context.combined_text == ""


def test_retrieval_cross_user_isolation(authz_api) -> None:
    factory = authz_api.session_factory
    user_a = authz_api.user_a_id
    user_b = authz_api.user_b_id
    course_id = authz_api.a_course_id

    with factory() as session:
        _seed_document_with_chunk(
            session,
            course_id,
            user_a,
            "Standard Course Material Content.",
        )
        session.add_all(
            [
                ProfileKnowledge(
                    user_id=user_a,
                    topic="User A Topic",
                    detail="User A background detail.",
                ),
                ProfileKnowledge(
                    user_id=user_b,
                    topic="User B Topic",
                    detail="User B secret background detail.",
                ),
            ]
        )
        session.commit()

    with factory() as session:
        context_a = assemble_generation_context(
            session,
            course_id=course_id,
            user_id=user_a,
            course_max_characters=1000,
        )
        assert "User A Topic" in context_a.combined_text
        assert "User B Topic" not in context_a.combined_text

        context_b = assemble_generation_context(
            session,
            course_id=course_id,
            user_id=user_b,
            course_max_characters=1000,
        )
        assert "User B Topic" in context_b.combined_text
        assert "User A Topic" not in context_b.combined_text
