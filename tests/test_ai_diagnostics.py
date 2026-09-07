"""SCRUM-206: a failed AI response is described in logs without quoting it."""

from dataclasses import replace

import pytest
from pydantic import BaseModel, ValidationError

from backend.app.config import settings
from schemas.ai_usage import ErrorCategory, GenerationType
from services.text_generation import TextGenerationError
from utils import ai_diagnostics
from utils.ai_diagnostics import (
    MAX_EXCERPT_CHARACTERS,
    MAX_RESPONSE_KEYS,
    ai_failure_fields,
    describe_response,
    describe_validation_error,
)

CANARY = "HIGHLY_CONFIDENTIAL_LECTURE_CHUNK_990011"


class Question(BaseModel):
    prompt: str
    options: list[str]


class Quiz(BaseModel):
    title: str
    questions: list[Question]


def enable_raw_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ai_diagnostics,
        "settings",
        replace(settings, ai_log_raw_response_on_failure=True),
    )


def validation_error_for(payload: object) -> ValidationError:
    try:
        Quiz.model_validate(payload)
    except ValidationError as exc:
        return exc
    raise AssertionError("payload was expected to be invalid")


def test_a_response_is_described_by_shape_and_never_by_its_values() -> None:
    fields = describe_response({"title": CANARY, "questions": [{"prompt": CANARY}]})

    assert fields["ai_response_type"] == "dict"
    assert fields["ai_response_keys"] == ["title", "questions"]
    assert fields["ai_response_bytes"] > 0
    assert CANARY not in repr(fields)


def test_response_keys_are_capped_and_model_authored_ones_are_masked() -> None:
    payload = {f"field_{index}": index for index in range(40)}
    payload[f"{CANARY} and more"] = 1
    payload["Uppercase"] = 1

    keys = describe_response(payload)["ai_response_keys"]

    assert len(keys) <= MAX_RESPONSE_KEYS
    assert CANARY not in repr(keys)
    assert describe_response({f"{CANARY} and more": 1})["ai_response_keys"] == ["*"]
    assert describe_response({"Uppercase": 1})["ai_response_keys"] == ["*"]


def test_the_digest_is_short_stable_and_distinguishes_payloads() -> None:
    first = describe_response({"a": 1})["ai_response_sha256"]
    again = describe_response({"a": 1})["ai_response_sha256"]
    other = describe_response({"a": 2})["ai_response_sha256"]

    assert first == again
    assert first != other
    assert len(first) == 16


def test_a_non_dict_response_reports_its_type_without_keys() -> None:
    fields = describe_response("Sure! Here is your quiz:")

    assert fields["ai_response_type"] == "str"
    assert "ai_response_keys" not in fields


def test_the_raw_response_is_found_through_a_wrapped_cause_chain() -> None:
    provider_error = TextGenerationError(
        "Ollama returned invalid JSON.",
        error_category=ErrorCategory.INVALID_STRUCTURE,
        raw_response="Sure! Here is your quiz:",
    )
    try:
        try:
            raise provider_error
        except TextGenerationError as inner:
            raise RuntimeError("wrapped once") from inner
    except RuntimeError as outer:
        wrapped = outer

    fields = ai_failure_fields(
        generation_type=GenerationType.QUIZ,
        error_category=ErrorCategory.INVALID_STRUCTURE,
        exc=wrapped,
    )

    assert fields["ai_response_type"] == "str"
    assert fields["ai_response_bytes"] == len(b"Sure! Here is your quiz:")


def test_a_validation_error_reports_locations_and_types_only() -> None:
    exc = validation_error_for({"title": "Quiz", "questions": [{"prompt": CANARY}]})

    described = describe_validation_error(exc)

    assert described == ["questions.0.options: missing"]
    assert CANARY not in repr(described)


def test_the_rejected_input_never_reaches_the_description() -> None:
    exc = validation_error_for({"title": CANARY, "questions": CANARY})

    described = describe_validation_error(exc)

    assert described
    assert CANARY not in repr(described)
    assert any(entry.startswith("questions:") for entry in described)


def test_a_model_authored_location_component_is_masked() -> None:
    class Scores(BaseModel):
        values: dict[str, int]

    try:
        Scores.model_validate({"values": {f"{CANARY} topic": "not an int"}})
    except ValidationError as exc:
        described = describe_validation_error(exc)

    assert described == ["values.*: int_parsing"]
    assert CANARY not in repr(described)


def test_a_plain_value_error_contributes_its_message() -> None:
    described = describe_validation_error(
        ValueError("Generated quiz does not contain the requested number of questions.")
    )

    assert described == [
        "Generated quiz does not contain the requested number of questions."
    ]


def test_a_value_error_message_is_redacted_and_truncated() -> None:
    described = describe_validation_error(
        ValueError("token=super-secret " + "x" * (MAX_EXCERPT_CHARACTERS + 500))
    )

    assert len(described[0]) <= MAX_EXCERPT_CHARACTERS
    assert "super-secret" not in described[0]
    assert "[REDACTED]" in described[0]


def test_the_excerpt_is_absent_while_the_setting_is_off() -> None:
    assert settings.ai_log_raw_response_on_failure is False

    fields = ai_failure_fields(
        generation_type=GenerationType.QUIZ,
        error_category=ErrorCategory.INVALID_STRUCTURE,
        response={"title": CANARY},
    )

    assert "ai_response_excerpt" not in fields
    assert CANARY not in repr(fields)


def test_the_excerpt_appears_truncated_and_redacted_once_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enable_raw_logging(monkeypatch)
    raw = "Authorization: Bearer sk-live-secret " + "y" * (MAX_EXCERPT_CHARACTERS + 500)

    fields = ai_failure_fields(
        generation_type=GenerationType.QUIZ,
        error_category=ErrorCategory.INVALID_STRUCTURE,
        raw_text=raw,
    )

    excerpt = fields["ai_response_excerpt"]
    assert len(excerpt) <= MAX_EXCERPT_CHARACTERS
    assert "sk-live-secret" not in excerpt
    assert "[REDACTED]" in excerpt


def test_the_assembled_fields_name_the_generation_and_the_provider() -> None:
    exc = validation_error_for({"title": "Quiz", "questions": [{"prompt": "q"}]})

    fields = ai_failure_fields(
        generation_type=GenerationType.QUIZ,
        provider="ollama",
        model="llama3",
        error_category=ErrorCategory.INVALID_STRUCTURE,
        response={"title": "Quiz"},
        exc=exc,
    )

    assert fields["event"] == "ai_generation_failed"
    assert fields["generation_type"] == "quiz"
    assert fields["provider"] == "ollama"
    assert fields["model"] == "llama3"
    assert fields["error_category"] == "invalid_structure"
    assert fields["exception_type"] == "ValidationError"
    assert fields["ai_validation_errors"]


def test_every_assembled_field_survives_the_formatter_allowlist() -> None:
    from backend.app.observability import _ALLOWED_FIELDS

    fields = ai_failure_fields(
        generation_type=GenerationType.QUIZ,
        provider="ollama",
        model="llama3",
        error_category=ErrorCategory.INVALID_STRUCTURE,
        response={"title": "Quiz"},
        exc=validation_error_for({"title": "Quiz"}),
    )

    unreachable = set(fields) - set(_ALLOWED_FIELDS) - {"event"}
    assert not unreachable, (
        "a diagnostic field outside the allowlist is dropped silently by JsonFormatter"
    )
