import logging

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models import Conversation, ConversationMessage, Course, User
from schemas.ai_usage import ErrorCategory, GenerationType
from schemas.prompt_context import PromptContext
from schemas.reverse_quiz import (
    Misconception,
    ReverseQuizEvaluation,
    ReverseQuizQuestion,
    ReverseQuizQuestionSet,
    ReverseQuizQuestionsResponse,
    ReverseQuizRequest,
    ReverseQuizResponse,
)
from services.ai_usage_logger import AiUsageLogger
from services.citations import sanitize_citation_markers
from services.generated_output import GeneratedOutputService
from services.prompt_context import resolve_prompt_context
from services.prompt_loader import PromptLoader
from services.retrieval_material import (
    RetrievalMaterialError,
    RetrievedCourseMaterial,
    load_retrieved_material,
)
from services.text_generation import TextGenerationProvider, model_identifier

logger = logging.getLogger(__name__)

REVERSE_QUIZ_MAX_CHARS = 12000
REVERSE_QUIZ_MAX_CHUNKS = 15

# Source-derived question generation reads more broadly than a single evaluation.
REVERSE_QUIZ_QUESTIONS_MAX_CHARS = 16000
REVERSE_QUIZ_QUESTIONS_MAX_CHUNKS = 24
REVERSE_QUIZ_QUESTION_COUNT = 5
REVERSE_QUIZ_HISTORY_MESSAGES = 12
REVERSE_QUIZ_HISTORY_MAX_CHARS = 2500

_EMPTY_MATERIAL = RetrievedCourseMaterial(
    text="",
    chunks_used=0,
    chunks_available=0,
    truncated=False,
    document_ids=(),
    citations=(),
)


class ReverseQuizService:
    PROMPT_TEMPLATE_NAME = "reverse_quiz"
    QUESTIONS_PROMPT_TEMPLATE_NAME = "reverse_quiz_questions"

    @classmethod
    def build_prompt(
        cls,
        *,
        topic: str,
        explanation: str,
        course_material: str,
        context: PromptContext,
        question: str = "",
    ) -> str:
        material_text = (
            course_material
            if course_material.strip()
            else "(No specific course material retrieved. Rely on general academic knowledge of the topic.)"
        )
        return PromptLoader.render(
            cls.PROMPT_TEMPLATE_NAME,
            {
                **context.as_variables(),
                "TOPIC": topic,
                "QUESTION": question,
                "STUDENT_EXPLANATION": explanation,
                "COURSE_MATERIAL": material_text,
            },
        )

    @classmethod
    def generate(
        cls,
        db: Session,
        *,
        course_id: int,
        user: User,
        request: ReverseQuizRequest,
        provider: TextGenerationProvider,
    ) -> ReverseQuizResponse:
        """Evaluate a student's explanation and provide grounded misconception feedback."""

        course = db.get(Course, course_id)
        if not course:
            raise ValueError(f"Course {course_id} not found")

        # 1. Retrieve course chunks based on the topic, the picked question, and
        #    the explanation itself.
        query = "\n".join(
            part
            for part in (request.topic, request.question or "", request.explanation)
            if part
        )
        try:
            material = load_retrieved_material(
                db,
                course_id,
                query=query,
                limit=REVERSE_QUIZ_MAX_CHUNKS,
                min_similarity=settings.retrieval_min_similarity,
                max_characters=REVERSE_QUIZ_MAX_CHARS,
                include_citations=True,
                provider=None,  # uses default embeddings
                store=None,  # uses default vector store
            )
        except RetrievalMaterialError:
            # Fall back to grading without context if no relevant material is indexed
            material = _EMPTY_MATERIAL

        prompt_context = resolve_prompt_context(db, course=course, user_id=user.id)
        prompt = cls.build_prompt(
            topic=request.topic,
            explanation=request.explanation,
            course_material=material.text,
            context=prompt_context,
            question=request.question or "",
        )

        metadata = None
        try:
            if hasattr(provider, "generate_json_with_metadata"):
                result, metadata = provider.generate_json_with_metadata(prompt)
            else:
                result = provider.generate_json(prompt)
        except Exception as exc:
            AiUsageLogger.log_failure(
                db,
                user_id=user.id,
                course_id=course_id,
                generation_type=GenerationType.REVERSE_QUIZ,
                error_category=getattr(
                    exc, "error_category", ErrorCategory.PROVIDER_ERROR
                ),
            )
            raise

        try:
            evaluation = ReverseQuizEvaluation.model_validate(result)
        except ValidationError:
            AiUsageLogger.log_failure(
                db,
                user_id=user.id,
                course_id=course_id,
                generation_type=GenerationType.REVERSE_QUIZ,
                error_category=ErrorCategory.INVALID_STRUCTURE,
                latency_ms=metadata.latency_ms if metadata else None,
            )
            raise ValueError(
                "Provider returned an invalid reverse quiz evaluation structure"
            )

        # 3. Apply citations to feedback and misconception details
        feedback_cited = sanitize_citation_markers(
            evaluation.feedback, material.citation_map
        )

        misconceptions = []
        for m in evaluation.misconceptions:
            m_cited = sanitize_citation_markers(m.detail, material.citation_map)
            misconceptions.append(
                Misconception(concept=m.concept, status=m.status, detail=m_cited.text)
            )

        AiUsageLogger.log_success(
            db,
            user_id=user.id,
            course_id=course_id,
            generation_type=GenerationType.REVERSE_QUIZ,
            metadata=metadata,
        )

        # 4. Save to GeneratedOutput for history and weak-topic aggregation
        response_model = ReverseQuizResponse(
            id=0,  # placeholder before commit
            course_id=course_id,
            topic=request.topic,
            explanation=request.explanation,
            feedback=feedback_cited.text,
            misconceptions=misconceptions,
            question=request.question,
        )

        output = GeneratedOutputService.record(
            db,
            course_id=course_id,
            user_id=user.id,
            model_used=model_identifier(metadata),
            output_type="reverse_quiz",
            content=response_model.model_dump_json(),
            commit=False,
        )

        response_model.id = output.id
        return response_model

    # ------------------------------------------------------------------
    # Source-derived practice questions
    # ------------------------------------------------------------------

    @staticmethod
    def _recent_conversation_digest(db: Session, course_id: int) -> str:
        """A compact, newest-last transcript of the course's recent chat turns."""
        rows = db.execute(
            select(ConversationMessage.role, ConversationMessage.content)
            .join(Conversation, Conversation.id == ConversationMessage.conversation_id)
            .where(Conversation.course_id == course_id)
            .order_by(ConversationMessage.id.desc())
            .limit(REVERSE_QUIZ_HISTORY_MESSAGES)
        ).all()
        if not rows:
            return ""

        lines = [
            f"{'Student' if role == 'user' else 'Assistant'}: {content.strip()}"
            for role, content in reversed(rows)
            if content and content.strip()
        ]
        digest = "\n".join(lines)
        if len(digest) > REVERSE_QUIZ_HISTORY_MAX_CHARS:
            digest = "…\n" + digest[-REVERSE_QUIZ_HISTORY_MAX_CHARS:]
        return digest

    @classmethod
    def suggest_questions(
        cls,
        db: Session,
        *,
        course_id: int,
        user: User,
        provider: TextGenerationProvider,
        count: int = REVERSE_QUIZ_QUESTION_COUNT,
    ) -> ReverseQuizQuestionsResponse:
        """Draft open-ended reverse-quiz questions from the course's own sources."""
        course = db.get(Course, course_id)
        if not course:
            raise ValueError(f"Course {course_id} not found")

        topics = " ".join(course.topics or [])
        query = f"{course.title} {course.subject_area or ''} {topics}".strip()
        try:
            material = load_retrieved_material(
                db,
                course_id,
                query=query or course.title,
                limit=REVERSE_QUIZ_QUESTIONS_MAX_CHUNKS,
                min_similarity=settings.retrieval_min_similarity,
                max_characters=REVERSE_QUIZ_QUESTIONS_MAX_CHARS,
                include_citations=False,
                provider=None,
                store=None,
            )
        except RetrievalMaterialError:
            material = _EMPTY_MATERIAL

        if not material.text.strip():
            return ReverseQuizQuestionsResponse(course_id=course_id, questions=[])

        prompt_context = resolve_prompt_context(db, course=course, user_id=user.id)
        prompt = PromptLoader.render(
            cls.QUESTIONS_PROMPT_TEMPLATE_NAME,
            {
                **prompt_context.as_variables(),
                "QUESTION_COUNT": str(count),
                "CONVERSATION_HISTORY": cls._recent_conversation_digest(db, course_id),
                "COURSE_MATERIAL": material.text,
            },
        )

        metadata = None
        try:
            if hasattr(provider, "generate_json_with_metadata"):
                result, metadata = provider.generate_json_with_metadata(prompt)
            else:
                result = provider.generate_json(prompt)
        except Exception as exc:
            AiUsageLogger.log_failure(
                db,
                user_id=user.id,
                course_id=course_id,
                generation_type=GenerationType.REVERSE_QUIZ,
                error_category=getattr(
                    exc, "error_category", ErrorCategory.PROVIDER_ERROR
                ),
            )
            raise

        try:
            generated = ReverseQuizQuestionSet.model_validate(result)
        except ValidationError:
            AiUsageLogger.log_failure(
                db,
                user_id=user.id,
                course_id=course_id,
                generation_type=GenerationType.REVERSE_QUIZ,
                error_category=ErrorCategory.INVALID_STRUCTURE,
                latency_ms=metadata.latency_ms if metadata else None,
            )
            raise ValueError("Provider returned an invalid reverse quiz question set")

        seen: set[str] = set()
        questions: list[ReverseQuizQuestion] = []
        for item in generated.questions:
            key = item.question.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            questions.append(item)
            if len(questions) >= count:
                break

        AiUsageLogger.log_success(
            db,
            user_id=user.id,
            course_id=course_id,
            generation_type=GenerationType.REVERSE_QUIZ,
            metadata=metadata,
        )

        return ReverseQuizQuestionsResponse(course_id=course_id, questions=questions)
