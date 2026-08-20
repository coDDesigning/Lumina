from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.models import (
    AiUsageLog,
    Course,
    DocumentChunk,
    Role,
    UploadedDocument,
    User,
)
from services.ai_tutor import AiTutorService
from services.flashcard import FlashcardService
from services.prompt_generator import PromptGeneratorService
from services.quiz import QuizService
from schemas.quiz import QuizDifficulty, QuizQuestionType, QuizRequest
from schemas.study_guide import StudyGuideRequest, SummaryFormat
from services.study_guide import StudyGuideService
from services.text_generation import GenerationMetadata


class PrivacySafeMockProvider:
    """Mock text generation provider returning structured outputs with token metadata."""

    def __init__(self) -> None:
        self.metadata = GenerationMetadata(
            provider="mock-gemini",
            model="gemini-2.5-flash",
            prompt_tokens=42,
            completion_tokens=84,
            total_tokens=126,
            latency_ms=250,
        )

    def generate_json_with_metadata(
        self, prompt: str
    ) -> tuple[dict[str, object], GenerationMetadata]:
        # Provide appropriate mock responses depending on which prompt is being evaluated
        if "FLASHCARD" in prompt.upper() or "flashcard" in prompt.lower():
            flashcard_data = {
                "deck_title": "Clean Flashcards",
                "card_count": 1,
                "flashcards": [
                    {
                        "card_number": 1,
                        "difficulty": "Easy",
                        "front": "Front question",
                        "back": "Back answer",
                    }
                ],
            }
            return flashcard_data, self.metadata

        if "QUIZ" in prompt.upper() or "quiz" in prompt.lower():
            quiz_data = {
                "title": "Clean Quiz",
                "questions": [
                    {
                        "question_number": i + 1,
                        "topic": "General",
                        "question": f"Question {i + 1}?",
                        "options": ["A", "B", "C", "D"],
                        "correct_option_index": 0,
                        "explanation": "Explanation",
                    }
                    for i in range(10)
                ],
            }
            return quiz_data, self.metadata

        if "PROMPT" in prompt.upper() or "generated_prompt" in prompt.lower():
            prompt_gen_data = {
                "generated_prompt": "Clean generated system prompt",
            }
            return prompt_gen_data, self.metadata

        # Default: study guide
        study_guide_data = {
            "title": "Clean Study Guide",
            "summary": "This is a clean summary without leaks.",
            "key_points": ["Key Point 1"],
            "important_terms": [{"term": "Term 1", "definition": "Definition 1"}],
            "common_mistakes": [{"mistake": "Mistake 1", "correction": "Correction 1"}],
            "exam_tips": {
                "lecture_based": ["Tip 1"],
                "ai_suggestions": ["Tip 2"],
            },
            "difficulty": {
                "level": "Medium",
                "reason": "Standard difficulty",
            },
            "estimated_study_time": "2 hours",
            "prerequisites": ["Basic reading"],
            "learning_objectives": ["Objective 1"],
            "coverage": {
                "status": "Complete",
                "estimated_completeness": 100,
            },
            "confidence_notes": "High confidence",
        }
        return study_guide_data, self.metadata

    def generate_text_with_metadata(
        self, prompt: str
    ) -> tuple[str, GenerationMetadata]:
        return "Clean answer without raw telemetry leaks.", self.metadata

    def generate_json(self, prompt: str) -> dict[str, object]:
        data, _ = self.generate_json_with_metadata(prompt)
        return data

    def generate_text(self, prompt: str) -> str:
        text, _ = self.generate_text_with_metadata(prompt)
        return text


def test_privacy_regression_asserts_raw_prompts_and_chunks_are_never_persisted(
    session_factory: sessionmaker[Session],
    retrieval_env,
) -> None:
    """Assert representative raw student prompts, secret tokens, and document chunks

    are NEVER written to the `ai_usage_logs` telemetry table.
    """
    secret_marker_student_prompt = "TOP_SECRET_STUDENT_QUESTION_778899"
    secret_marker_course_chunk = "HIGHLY_CONFIDENTIAL_LECTURE_CHUNK_990011"
    secret_marker_description = "SECRET_PROJECT_DESCRIPTION_554433"

    with session_factory() as session:
        role = session.scalar(select(Role).where(Role.name == "user"))
        if not role:
            role = Role(name="user")
            session.add(role)
            session.flush()

        user = User(
            name="Privacy Test User",
            email="privacy-user@example.com",
            password_hash="hash",
            role=role,
        )
        course = Course(
            owner=user,
            title="Confidential Course",
            semester="Fall 2026",
        )
        doc = UploadedDocument(
            id=uuid4(),
            original_file_name="confidential.txt",
            file_type="txt",
            mime_type="text/plain",
            file_size=100,
            file_hash="a" * 64,
            uploader=user,
            course=course,
            status="ready",
            storage_provider="local:privacy",
            storage_key="privacy/test.txt",
        )
        chunk = DocumentChunk(
            document=doc,
            course=course,
            chunk_index=0,
            page_number=1,
            end_page_number=1,
            text=f"Intro to material with {secret_marker_course_chunk} embedded.",
        )
        session.add_all((user, course, doc, chunk))
        session.flush()
        retrieval_env.index(session, doc, [chunk])
        session.commit()

        user_id = user.id
        course_id = course.id

    provider = PrivacySafeMockProvider()

    with session_factory() as session:
        # 1. Study Guide generation
        StudyGuideService.generate(
            session,
            course_id,
            StudyGuideRequest(
                summary_format=SummaryFormat.COMPREHENSIVE,
                topic_focus="All Topics",
            ),
            provider,
            user_id=user_id,
        )

        # 2. Quiz generation
        QuizService.generate(
            session,
            course_id,
            QuizRequest(
                question_count=10,
                question_type=QuizQuestionType.MULTIPLE_CHOICE,
                difficulty=QuizDifficulty.MEDIUM,
                topic_focus="All Topics",
            ),
            provider,
            user_id=user_id,
        )

        # 3. Flashcard generation
        FlashcardService.generate(session, course_id, provider, user_id=user_id)

        # 4. AI Tutor generation (with secret question)
        AiTutorService.generate(
            session,
            course_id,
            f"Can you explain {secret_marker_student_prompt}?",
            provider,
            user_id=user_id,
        )

        # 5. Prompt Generator generation (with secret description)
        PromptGeneratorService.generate(
            f"Generate a prompt about {secret_marker_description}",
            provider,
            db=session,
            user_id=user_id,
        )

        session.commit()

    # Query all telemetry logs and verify no secret strings exist anywhere
    with session_factory() as session:
        logs = session.scalars(
            select(AiUsageLog).where(AiUsageLog.user_id == user_id)
        ).all()
        assert len(logs) == 5

        for log in logs:
            # Check all string columns of the telemetry row
            for col_val in (
                log.generation_type,
                log.provider,
                log.model,
                log.error_category,
            ):
                if col_val is not None:
                    assert secret_marker_student_prompt not in col_val
                    assert secret_marker_course_chunk not in col_val
                    assert secret_marker_description not in col_val
