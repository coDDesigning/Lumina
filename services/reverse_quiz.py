import json
import logging
from pydantic import ValidationError
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models import Course, GeneratedOutput, User
from schemas.ai_usage import ErrorCategory, GenerationType
from schemas.quiz import OpenEndedAnswer
from schemas.prompt_context import PromptContext
from schemas.quiz_attempt import OpenEndedGradingResponse
from schemas.reverse_quiz import ReverseQuizRequest, ReverseQuizResponse, Misconception
from services.ai_usage_logger import AiUsageLogger
from services.citations import sanitize_citation_markers, SuppliedCitation
from services.prompt_context import resolve_prompt_context
from services.quiz_grading import QuizGradingService
from services.retrieval_material import (
    RetrievedCourseMaterial,
    load_retrieved_material,
    RetrievalMaterialError,
)
from services.text_generation import TextGenerationProvider

logger = logging.getLogger(__name__)

# Re-use limits from study guide or similar feature, or define specific ones
REVERSE_QUIZ_MAX_CHARS = 12000
REVERSE_QUIZ_MAX_CHUNKS = 15


class ReverseQuizService:
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

        # 1. Retrieve course chunks based on the topic and explanation
        query = f"{request.topic}\n{request.explanation}"
        try:
            material = load_retrieved_material(
                db,
                course_id,
                query=query,
                limit=REVERSE_QUIZ_MAX_CHUNKS,
                min_similarity=settings.RETRIEVAL_MIN_SIMILARITY,
                max_characters=REVERSE_QUIZ_MAX_CHARS,
                include_citations=True,
                provider=None, # uses default embeddings
                store=None, # uses default vector store
            )
        except RetrievalMaterialError:
            # Fall back to grading without context if no relevant material is indexed
            material = RetrievedCourseMaterial(text="", chunks_used=0, chunks_available=0, truncated=False, document_ids=(), citations=())

        # 2. Build the "grading" request by using the QuizGradingService prompt
        # We construct a virtual open-ended answer where the reference answer is the retrieved material.
        class VirtualQuestion:
            question_text = f"Topic: {request.topic}"
        
        question = VirtualQuestion()
        answer = OpenEndedAnswer(reference_answer=material.text if material.text else "(No specific course material retrieved. Rely on general knowledge of the topic.)")

        pending = [(0, question, request.explanation, answer)]
        prompt_context = resolve_prompt_context(db, course=course, user_id=user.id)
        prompt = QuizGradingService.build_prompt(pending, context=prompt_context)

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
                generation_type=GenerationType.QUIZ_GRADING,
                error_category=getattr(exc, "error_category", ErrorCategory.PROVIDER_ERROR),
            )
            raise

        try:
            verdicts = OpenEndedGradingResponse.model_validate(result)
        except ValidationError:
            AiUsageLogger.log_failure(
                db,
                user_id=user.id,
                course_id=course_id,
                generation_type=GenerationType.QUIZ_GRADING,
                error_category=ErrorCategory.INVALID_STRUCTURE,
                latency_ms=metadata.latency_ms if metadata else None,
            )
            raise ValueError("Provider returned an invalid grading structure")

        verdict = verdicts.verdicts[0]
        
        # 3. Apply citations to feedback and misconception details
        feedback_cited = sanitize_citation_markers(verdict.feedback, material.citation_map)
        
        misconceptions = []
        for m in verdict.misconceptions:
            m_cited = sanitize_citation_markers(m.detail, material.citation_map)
            misconceptions.append(
                Misconception(
                    concept=m.concept,
                    status=m.status,
                    detail=m_cited.text
                )
            )

        AiUsageLogger.log_success(
            db,
            user_id=user.id,
            course_id=course_id,
            generation_type=GenerationType.QUIZ_GRADING,
            metadata=metadata,
        )

        # 4. Save to GeneratedOutput for history and weak-topic aggregation
        response_model = ReverseQuizResponse(
            id=0, # placeholder before commit
            course_id=course_id,
            topic=request.topic,
            explanation=request.explanation,
            feedback=feedback_cited.text,
            misconceptions=misconceptions,
        )

        output = GeneratedOutput(
            course_id=course_id,
            user_id=user.id,
            model_used=provider.name,
            output_type="reverse_quiz",
            content=response_model.model_dump_json(),
        )
        db.add(output)
        db.flush()
        
        response_model.id = output.id
        return response_model
