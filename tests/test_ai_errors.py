"""The exception-to-response mapping every AI route funnels through.

Classification walks ``__cause__`` only, so these tests wrap deliberately with
``raise ... from exc`` exactly the way the feature services do.
"""

import pytest
from fastapi import status

from schemas.ai_usage import ErrorCategory
from services.embeddings import (
    EmbeddingAuthError,
    EmbeddingConfigurationError,
    EmbeddingConnectionError,
    EmbeddingDimensionMismatchError,
    EmbeddingInvalidResponseError,
    EmbeddingProviderError,
    EmbeddingRateLimitError,
    EmbeddingTimeoutError,
)
from services.retrieval_material import (
    MaterialRetrievalError,
    MaterialRetrievalRateLimitError,
    MaterialRetrievalTimeoutError,
    NoRelevantMaterialError,
)
from services.text_generation import (
    TextGenerationConnectionError,
    TextGenerationError,
    TextGenerationRateLimitError,
    TextGenerationTimeoutError,
)
from services.vector_store import VectorStoreConfigurationError, VectorStoreError
from utils.ai_errors import (
    NO_READY_MATERIAL_MESSAGE,
    NO_RELEVANT_MATERIAL_MESSAGE,
    PUBLIC_MESSAGES,
    STATUS_CODES,
    AiErrorCode,
    CourseMaterialUnavailableError,
    InvalidGeneratedStructureError,
    NoRelevantCourseMaterialError,
    RetrievalRateLimitedError,
    RetrievalTimeoutError,
    RetrievalUnavailableError,
    ai_generation_http_exception,
    classify_generation_error,
)


# Raw failure text can name hosts, models and payloads; it must never be returned.
SECRET = "embed-host-9000 leaked internal detail"


def test_every_code_has_a_public_message_and_a_status() -> None:
    for code in AiErrorCode:
        assert code in PUBLIC_MESSAGES
        assert code in STATUS_CODES


@pytest.mark.parametrize(
    ("exception", "expected"),
    [
        (
            CourseMaterialUnavailableError(NO_READY_MATERIAL_MESSAGE),
            AiErrorCode.NO_READY_MATERIAL,
        ),
        (
            NoRelevantCourseMaterialError(NO_RELEVANT_MATERIAL_MESSAGE),
            AiErrorCode.NO_RELEVANT_MATERIAL,
        ),
        (
            InvalidGeneratedStructureError("bad"),
            AiErrorCode.INVALID_GENERATED_STRUCTURE,
        ),
        (TextGenerationConnectionError("down"), AiErrorCode.PROVIDER_UNAVAILABLE),
        (TextGenerationTimeoutError("slow"), AiErrorCode.PROVIDER_TIMEOUT),
        (TextGenerationRateLimitError("busy"), AiErrorCode.PROVIDER_RATE_LIMITED),
        (RetrievalTimeoutError("slow"), AiErrorCode.PROVIDER_TIMEOUT),
        (RetrievalRateLimitedError("busy"), AiErrorCode.PROVIDER_RATE_LIMITED),
        (RetrievalUnavailableError("down"), AiErrorCode.RETRIEVAL_UNAVAILABLE),
        (TextGenerationError("nothing specific"), AiErrorCode.GENERATION_FAILED),
    ],
)
def test_classification_recognizes_each_marker(
    exception: BaseException,
    expected: AiErrorCode,
) -> None:
    assert classify_generation_error(exception) is expected


def test_a_relevance_miss_is_never_mistaken_for_missing_material() -> None:
    """The two states are different: one has no material, the other no match."""
    assert not isinstance(
        NoRelevantCourseMaterialError("x"), CourseMaterialUnavailableError
    )
    assert (
        classify_generation_error(NoRelevantMaterialError(NO_RELEVANT_MATERIAL_MESSAGE))
        is AiErrorCode.NO_RELEVANT_MATERIAL
    )


def test_retrieval_subclasses_are_classified_before_their_base() -> None:
    assert isinstance(RetrievalTimeoutError("x"), RetrievalUnavailableError)
    assert isinstance(RetrievalRateLimitedError("x"), RetrievalUnavailableError)
    assert (
        classify_generation_error(RetrievalTimeoutError("x"))
        is AiErrorCode.PROVIDER_TIMEOUT
    )
    assert (
        classify_generation_error(RetrievalRateLimitedError("x"))
        is AiErrorCode.PROVIDER_RATE_LIMITED
    )


@pytest.mark.parametrize(
    ("wrapper", "expected"),
    [
        (MaterialRetrievalTimeoutError, AiErrorCode.PROVIDER_TIMEOUT),
        (MaterialRetrievalRateLimitError, AiErrorCode.PROVIDER_RATE_LIMITED),
        (MaterialRetrievalError, AiErrorCode.RETRIEVAL_UNAVAILABLE),
    ],
)
def test_retrieval_material_wrappers_classify_through_the_cause_chain(
    wrapper: type[Exception],
    expected: AiErrorCode,
) -> None:
    try:
        try:
            raise EmbeddingProviderError("provider detail naming a host")
        except Exception as exc:
            raise wrapper("curated") from exc
    except Exception as exc:
        assert classify_generation_error(exc) is expected


@pytest.mark.parametrize(
    "cause",
    [
        EmbeddingTimeoutError(SECRET),
        EmbeddingRateLimitError(SECRET),
        EmbeddingConnectionError(SECRET),
        EmbeddingProviderError(SECRET),
        EmbeddingInvalidResponseError(SECRET),
        EmbeddingDimensionMismatchError(SECRET),
        EmbeddingAuthError(SECRET),
        EmbeddingConfigurationError(SECRET),
        VectorStoreError(SECRET),
        VectorStoreConfigurationError(SECRET),
    ],
)
def test_raw_retrieval_failure_text_never_reaches_the_response(
    cause: BaseException,
) -> None:
    try:
        raise MaterialRetrievalError("curated") from cause
    except Exception as exc:
        http_exception = ai_generation_http_exception(exc, feature="study_guide")

    assert http_exception.detail == PUBLIC_MESSAGES[AiErrorCode.RETRIEVAL_UNAVAILABLE]
    assert str(cause) not in str(http_exception.detail)


def test_no_relevant_material_answers_with_conflict() -> None:
    http_exception = ai_generation_http_exception(
        NoRelevantMaterialError(NO_RELEVANT_MATERIAL_MESSAGE), feature="study_guide"
    )

    assert http_exception.status_code == status.HTTP_409_CONFLICT
    assert http_exception.detail == NO_RELEVANT_MATERIAL_MESSAGE
    assert http_exception.status_code != STATUS_CODES[AiErrorCode.NO_READY_MATERIAL]


def test_retrieval_unavailable_answers_with_service_unavailable() -> None:
    http_exception = ai_generation_http_exception(
        MaterialRetrievalError("curated"), feature="study_guide"
    )

    assert http_exception.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


def test_new_error_categories_are_recordable() -> None:
    assert ErrorCategory.NO_RELEVANT_MATERIAL.value == "no_relevant_material"
    assert ErrorCategory.RETRIEVAL_ERROR.value == "retrieval_error"
