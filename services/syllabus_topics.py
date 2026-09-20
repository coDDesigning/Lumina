import re
import string
from collections.abc import Sequence

from pydantic import ValidationError
from sqlalchemy.orm import Session

from backend.app.models import User
from schemas.ai_usage import ErrorCategory, GenerationType
from schemas.course import MAX_TOPIC_LENGTH, MAX_TOPICS
from schemas.syllabus_topics import (
    GeneratedSyllabusTopic,
    GeneratedSyllabusTopicsResponse,
    SuggestedTopic,
)
from services.ai_usage_logger import AiUsageLogger
from services.exam_topics import canonical_topic_key
from services.prompt_context import resolve_prompt_context
from services.prompt_loader import PromptLoader
from services.text_generation import (
    TextGenerationError,
    get_text_generation_provider,
    resolve_effective_model,
    with_template_temperature,
)

PROMPT_TEMPLATE_NAME = "syllabus_topics"

ADMINISTRATIVE_TOPIC_LABELS = (
    "Midterm exam",
    "Final exam",
    "Exam",
    "Quiz",
    "Office hours",
    "Grading",
    "Attendance",
    "Reading week",
    "Holiday",
    "Break",
)
ADMINISTRATIVE_TOPIC_LABEL_VARIANTS = (
    "Midterm exams",
    "Final exams",
    "Exams",
    "Quizzes",
    "Office hour",
    "Reading weeks",
    "Holidays",
    "Breaks",
)

_WHITESPACE_RE = re.compile(r"\s+")
_ADMINISTRATIVE_STRIP_CHARS = string.whitespace + string.punctuation


def _normalized_name(name: str) -> str:
    return _WHITESPACE_RE.sub(" ", name.strip()).strip()


def _normalized_administrative_name(name: str) -> str:
    folded = name.casefold()
    collapsed = _WHITESPACE_RE.sub(" ", folded)
    return collapsed.strip(_ADMINISTRATIVE_STRIP_CHARS)


ADMINISTRATIVE_TOPIC_NAMES = frozenset(
    _normalized_administrative_name(label)
    for label in ADMINISTRATIVE_TOPIC_LABELS + ADMINISTRATIVE_TOPIC_LABEL_VARIANTS
)


def _cleaned_weight(weight: float | None) -> int | None:
    if weight is None or not (0 < weight <= 100):
        return None
    return round(weight)


def clean_suggested_topics(
    raw: Sequence[GeneratedSyllabusTopic],
) -> list[SuggestedTopic]:
    candidates: list[tuple[str, int | None]] = []
    for topic in raw:
        name = _normalized_name(topic.name)
        if not name or len(name) > MAX_TOPIC_LENGTH:
            continue
        candidates.append((name, _cleaned_weight(topic.weight_percent)))

    cleaned: list[SuggestedTopic] = []
    seen_keys: set[str] = set()
    for name, weight in candidates:
        key = canonical_topic_key(name)
        if (
            not key
            or key in seen_keys
            or _normalized_administrative_name(name) in ADMINISTRATIVE_TOPIC_NAMES
        ):
            continue
        seen_keys.add(key)
        cleaned.append(SuggestedTopic(name=name, weight_percent=weight))
        if len(cleaned) >= MAX_TOPICS:
            break

    return cleaned


def suggest_topics(
    db: Session,
    *,
    user_id: int,
    preferred_model: str | None,
    text: str,
) -> list[SuggestedTopic]:
    def log_failure(category: ErrorCategory, **extra) -> None:
        AiUsageLogger.log_failure(
            db,
            user_id=user_id,
            course_id=None,
            generation_type=GenerationType.SYLLABUS_TOPICS,
            error_category=category,
            **extra,
        )
        AiUsageLogger.commit(db)

    db_user = db.get(User, user_id)
    effective_model = resolve_effective_model(None, preferred_model, user=db_user)
    provider = get_text_generation_provider(
        effective_model=effective_model,
        user=db_user,
        require_json_mode=True,
    )
    provider = with_template_temperature(
        provider, PromptLoader.temperature_for(PROMPT_TEMPLATE_NAME)
    )

    prompt_context = resolve_prompt_context(db, user_id=user_id)
    prompt = PromptLoader.render(
        PROMPT_TEMPLATE_NAME,
        {**prompt_context.as_variables(), "SYLLABUS_TEXT": text.strip()},
    )

    metadata = None
    try:
        result, metadata = provider.generate_json_with_metadata(prompt)
    except TextGenerationError as exc:
        log_failure(
            getattr(exc, "error_category", ErrorCategory.PROVIDER_ERROR), exc=exc
        )
        raise

    try:
        validated = GeneratedSyllabusTopicsResponse.model_validate(result)
    except ValidationError as exc:
        log_failure(
            ErrorCategory.INVALID_STRUCTURE,
            metadata=metadata,
            response=result,
            exc=exc,
        )
        raise TextGenerationError(
            "Generated syllabus topics have an invalid structure.",
            error_category=ErrorCategory.INVALID_STRUCTURE,
        ) from exc

    cleaned = clean_suggested_topics(validated.topics)

    AiUsageLogger.log_success(
        db,
        user_id=user_id,
        course_id=None,
        generation_type=GenerationType.SYLLABUS_TOPICS,
        metadata=metadata,
    )
    AiUsageLogger.commit(db)

    return cleaned
