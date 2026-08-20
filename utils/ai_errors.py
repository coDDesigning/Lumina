import logging
from collections.abc import Iterator
from enum import Enum

from fastapi import HTTPException, status

from schemas.ai_usage import ErrorCategory
from services.text_generation import TextGenerationConnectionError

logger = logging.getLogger(__name__)


class CourseMaterialUnavailableError(RuntimeError):
    pass


class InvalidGeneratedStructureError(RuntimeError):
    pass


class InsufficientCreditsError(RuntimeError):
    pass


class AiErrorCode(str, Enum):
    NO_READY_MATERIAL = "no_ready_material"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_RATE_LIMITED = "provider_rate_limited"
    INVALID_GENERATED_STRUCTURE = "invalid_generated_structure"
    INSUFFICIENT_CREDITS = "insufficient_credits"
    GENERATION_FAILED = "generation_failed"


NO_READY_MATERIAL_MESSAGE = "No processed course material is available for this course."

PUBLIC_MESSAGES: dict[AiErrorCode, str] = {
    AiErrorCode.NO_READY_MATERIAL: NO_READY_MATERIAL_MESSAGE,
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
    AiErrorCode.GENERATION_FAILED: (
        "The request could not be completed. Please try again later."
    ),
}

STATUS_CODES: dict[AiErrorCode, int] = {
    AiErrorCode.NO_READY_MATERIAL: status.HTTP_400_BAD_REQUEST,
    AiErrorCode.PROVIDER_UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
    AiErrorCode.PROVIDER_TIMEOUT: status.HTTP_504_GATEWAY_TIMEOUT,
    AiErrorCode.PROVIDER_RATE_LIMITED: status.HTTP_429_TOO_MANY_REQUESTS,
    AiErrorCode.INVALID_GENERATED_STRUCTURE: status.HTTP_500_INTERNAL_SERVER_ERROR,
    AiErrorCode.INSUFFICIENT_CREDITS: status.HTTP_402_PAYMENT_REQUIRED,
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
        if isinstance(error, InsufficientCreditsError):
            return AiErrorCode.INSUFFICIENT_CREDITS
        if isinstance(error, CourseMaterialUnavailableError):
            return AiErrorCode.NO_READY_MATERIAL
        if isinstance(error, InvalidGeneratedStructureError):
            return AiErrorCode.INVALID_GENERATED_STRUCTURE
        if isinstance(error, TextGenerationConnectionError):
            return AiErrorCode.PROVIDER_UNAVAILABLE

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

    return HTTPException(status_code=status_code, detail=PUBLIC_MESSAGES[code])
