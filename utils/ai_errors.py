import logging
from collections.abc import Iterator
from enum import Enum

from fastapi import HTTPException, status

from schemas.ai_usage import ErrorCategory
from services.text_generation import (
    IncompatibleModelError,
    TextGenerationConnectionError,
    UnavailableModelError,
)

logger = logging.getLogger(__name__)

ERROR_CODE_HEADER = "X-Error-Code"


class CourseMaterialUnavailableError(RuntimeError):
    pass


class InvalidGeneratedStructureError(RuntimeError):
    pass


class NoRelevantCourseMaterialError(RuntimeError):
    """The course has material, but none of it matched the request.

    Deliberately not a ``CourseMaterialUnavailableError``: an empty corpus and a
    query nothing answers are different states with different remedies.
    """


class CourseMaterialNotIndexedError(RuntimeError):
    """The course has processed material that is not in the vector store.

    Distinct from both an empty corpus and a relevance miss. Uploading changes
    nothing, and no query will ever match, so telling the student to broaden
    their topic sends them after a problem they cannot reach.
    """


class RetrievalUnavailableError(RuntimeError):
    pass


class RetrievalTimeoutError(RetrievalUnavailableError):
    pass


class RetrievalRateLimitedError(RetrievalUnavailableError):
    pass


class InsufficientCreditsError(RuntimeError):
    pass


class SourceDocumentNotReadyError(RuntimeError):
    """A document the request explicitly selected has not finished processing.

    Distinct from every material error: the course has material, the request
    matched it, and the remedy is neither uploading nor broadening the topic.
    It is waiting.
    """


class ExamAnalysisRequiredError(RuntimeError):
    """The operation needs a completed exam topic analysis that does not exist.

    A conflict rather than a not-found, because the course is fine and the
    remedy is a named next action that a 404 would hide.
    """


class ExamTopicSelectionRequiredError(RuntimeError):
    """An exam plan was requested without choosing any topic to study."""


class ExamTopicNotDiscoveredError(RuntimeError):
    """A selected topic does not belong to the analysis it was selected from."""


class ExamDateMissingError(RuntimeError):
    """The course has no exam date, so there is no exam to plan for."""


class ExamDateNotFutureError(RuntimeError):
    """The course exam date has already passed, so a first plan cannot start.

    Only the first plan is gated. An existing plan stays readable forever, as
    a study resource does not expire with the exam it was built for.
    """


class AiErrorCode(str, Enum):
    NO_READY_MATERIAL = "no_ready_material"
    NO_RELEVANT_MATERIAL = "no_relevant_material"
    MATERIAL_NOT_INDEXED = "material_not_indexed"
    RETRIEVAL_UNAVAILABLE = "retrieval_unavailable"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_RATE_LIMITED = "provider_rate_limited"
    INVALID_GENERATED_STRUCTURE = "invalid_generated_structure"
    INSUFFICIENT_CREDITS = "insufficient_credits"
    UNAVAILABLE_MODEL = "unavailable_model"
    INCOMPATIBLE_MODEL = "incompatible_model"
    SOURCE_NOT_READY = "source_not_ready"
    EXAM_DATE_MISSING = "exam_date_missing"
    EXAM_DATE_NOT_FUTURE = "exam_date_not_future"
    EXAM_ANALYSIS_REQUIRED = "exam_analysis_required"
    EXAM_TOPIC_SELECTION_REQUIRED = "exam_topic_selection_required"
    EXAM_TOPIC_NOT_DISCOVERED = "exam_topic_not_discovered"
    GENERATION_FAILED = "generation_failed"


NO_READY_MATERIAL_MESSAGE = "No processed course material is available for this course."
NO_RELEVANT_MATERIAL_MESSAGE = (
    "No course material matched this request. Try a broader topic focus."
)
MATERIAL_NOT_INDEXED_MESSAGE = (
    "This course's material is not searchable yet. If it does not become "
    "available shortly, its documents need to be processed again."
)
SOURCE_NOT_READY_MESSAGE = (
    "A selected document is still being processed. Try again once it is ready."
)
EXAM_DATE_MISSING_MESSAGE = (
    "Set an exam date for this course before creating an exam plan."
)
EXAM_DATE_NOT_FUTURE_MESSAGE = (
    "This course's exam date is not in the future. Update it before creating "
    "an exam plan."
)
EXAM_ANALYSIS_REQUIRED_MESSAGE = (
    "Analyse this course's exam sources before creating an exam plan."
)
EXAM_TOPIC_SELECTION_REQUIRED_MESSAGE = (
    "Select at least one topic to study before creating an exam plan."
)
EXAM_TOPIC_NOT_DISCOVERED_MESSAGE = (
    "A selected topic is not part of this analysis. Review the discovered "
    "topics and try again."
)

PUBLIC_MESSAGES: dict[AiErrorCode, str] = {
    AiErrorCode.NO_READY_MATERIAL: NO_READY_MATERIAL_MESSAGE,
    AiErrorCode.NO_RELEVANT_MATERIAL: NO_RELEVANT_MATERIAL_MESSAGE,
    AiErrorCode.MATERIAL_NOT_INDEXED: MATERIAL_NOT_INDEXED_MESSAGE,
    AiErrorCode.RETRIEVAL_UNAVAILABLE: (
        "Course search is temporarily unavailable. Please try again later."
    ),
    AiErrorCode.PROVIDER_UNAVAILABLE: (
        "The AI service is currently unavailable. Please try again later."
    ),
    AiErrorCode.PROVIDER_TIMEOUT: (
        "The AI service did not respond in time. Please try again."
    ),
    AiErrorCode.PROVIDER_RATE_LIMITED: (
        "The AI service is busy. Please try again in a few moments."
    ),
    AiErrorCode.INVALID_GENERATED_STRUCTURE: (
        "The AI service returned an unusable result. Please try again."
    ),
    AiErrorCode.INSUFFICIENT_CREDITS: (
        "You do not have enough credits to complete this generation."
    ),
    AiErrorCode.UNAVAILABLE_MODEL: ("The requested AI model is not available."),
    AiErrorCode.INCOMPATIBLE_MODEL: (
        "The requested AI model does not support the required output format."
    ),
    AiErrorCode.SOURCE_NOT_READY: SOURCE_NOT_READY_MESSAGE,
    AiErrorCode.EXAM_DATE_MISSING: EXAM_DATE_MISSING_MESSAGE,
    AiErrorCode.EXAM_DATE_NOT_FUTURE: EXAM_DATE_NOT_FUTURE_MESSAGE,
    AiErrorCode.EXAM_ANALYSIS_REQUIRED: EXAM_ANALYSIS_REQUIRED_MESSAGE,
    AiErrorCode.EXAM_TOPIC_SELECTION_REQUIRED: (EXAM_TOPIC_SELECTION_REQUIRED_MESSAGE),
    AiErrorCode.EXAM_TOPIC_NOT_DISCOVERED: EXAM_TOPIC_NOT_DISCOVERED_MESSAGE,
    AiErrorCode.GENERATION_FAILED: (
        "The request could not be completed. Please try again later."
    ),
}

STATUS_CODES: dict[AiErrorCode, int] = {
    AiErrorCode.NO_READY_MATERIAL: status.HTTP_400_BAD_REQUEST,
    AiErrorCode.NO_RELEVANT_MATERIAL: status.HTTP_409_CONFLICT,
    AiErrorCode.MATERIAL_NOT_INDEXED: status.HTTP_409_CONFLICT,
    AiErrorCode.RETRIEVAL_UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
    AiErrorCode.PROVIDER_UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
    AiErrorCode.PROVIDER_TIMEOUT: status.HTTP_504_GATEWAY_TIMEOUT,
    AiErrorCode.PROVIDER_RATE_LIMITED: status.HTTP_429_TOO_MANY_REQUESTS,
    AiErrorCode.INVALID_GENERATED_STRUCTURE: status.HTTP_500_INTERNAL_SERVER_ERROR,
    AiErrorCode.INSUFFICIENT_CREDITS: status.HTTP_402_PAYMENT_REQUIRED,
    AiErrorCode.UNAVAILABLE_MODEL: status.HTTP_400_BAD_REQUEST,
    AiErrorCode.INCOMPATIBLE_MODEL: status.HTTP_400_BAD_REQUEST,
    AiErrorCode.SOURCE_NOT_READY: status.HTTP_409_CONFLICT,
    AiErrorCode.EXAM_DATE_MISSING: status.HTTP_400_BAD_REQUEST,
    AiErrorCode.EXAM_DATE_NOT_FUTURE: status.HTTP_400_BAD_REQUEST,
    AiErrorCode.EXAM_ANALYSIS_REQUIRED: status.HTTP_409_CONFLICT,
    AiErrorCode.EXAM_TOPIC_SELECTION_REQUIRED: status.HTTP_409_CONFLICT,
    AiErrorCode.EXAM_TOPIC_NOT_DISCOVERED: status.HTTP_409_CONFLICT,
    AiErrorCode.GENERATION_FAILED: status.HTTP_500_INTERNAL_SERVER_ERROR,
}


def _exception_chain(exc: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__


def classify_generation_error(exc: BaseException) -> AiErrorCode:
    for error in _exception_chain(exc):
        if isinstance(error, IncompatibleModelError):
            return AiErrorCode.INCOMPATIBLE_MODEL
        if isinstance(error, UnavailableModelError):
            return AiErrorCode.UNAVAILABLE_MODEL
        if isinstance(error, InsufficientCreditsError):
            return AiErrorCode.INSUFFICIENT_CREDITS
        if isinstance(error, SourceDocumentNotReadyError):
            return AiErrorCode.SOURCE_NOT_READY
        if isinstance(error, ExamDateMissingError):
            return AiErrorCode.EXAM_DATE_MISSING
        if isinstance(error, ExamDateNotFutureError):
            return AiErrorCode.EXAM_DATE_NOT_FUTURE
        if isinstance(error, ExamAnalysisRequiredError):
            return AiErrorCode.EXAM_ANALYSIS_REQUIRED
        if isinstance(error, ExamTopicSelectionRequiredError):
            return AiErrorCode.EXAM_TOPIC_SELECTION_REQUIRED
        if isinstance(error, ExamTopicNotDiscoveredError):
            return AiErrorCode.EXAM_TOPIC_NOT_DISCOVERED
        if isinstance(error, CourseMaterialUnavailableError):
            return AiErrorCode.NO_READY_MATERIAL
        if isinstance(error, CourseMaterialNotIndexedError):
            return AiErrorCode.MATERIAL_NOT_INDEXED
        if isinstance(error, NoRelevantCourseMaterialError):
            return AiErrorCode.NO_RELEVANT_MATERIAL
        if isinstance(error, InvalidGeneratedStructureError):
            return AiErrorCode.INVALID_GENERATED_STRUCTURE
        if isinstance(error, TextGenerationConnectionError):
            return AiErrorCode.PROVIDER_UNAVAILABLE
        if isinstance(error, RetrievalTimeoutError):
            return AiErrorCode.PROVIDER_TIMEOUT
        if isinstance(error, RetrievalRateLimitedError):
            return AiErrorCode.PROVIDER_RATE_LIMITED
        if isinstance(error, RetrievalUnavailableError):
            return AiErrorCode.RETRIEVAL_UNAVAILABLE

        category = getattr(error, "error_category", None)
        if category == ErrorCategory.TIMEOUT.value:
            return AiErrorCode.PROVIDER_TIMEOUT
        if category == ErrorCategory.RATE_LIMIT.value:
            return AiErrorCode.PROVIDER_RATE_LIMITED
        if category == ErrorCategory.INVALID_STRUCTURE.value:
            return AiErrorCode.INVALID_GENERATED_STRUCTURE
        if category == ErrorCategory.INSUFFICIENT_CREDITS.value:
            return AiErrorCode.INSUFFICIENT_CREDITS

    return AiErrorCode.GENERATION_FAILED


def ai_generation_http_exception(exc: BaseException, *, feature: str) -> HTTPException:
    code = classify_generation_error(exc)
    status_code = STATUS_CODES[code]

    if status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
        logger.error("%s generation failed with %s", feature, code.value, exc_info=exc)
    else:
        logger.warning("%s generation rejected with %s", feature, code.value)

    return HTTPException(
        status_code=status_code,
        detail=PUBLIC_MESSAGES[code],
        headers={ERROR_CODE_HEADER: code.value},
    )
