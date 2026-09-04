"""Shared scaffolding for tests that run the same proof across every generation feature."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from types import ModuleType
from typing import Any

from sqlalchemy import select

import routes.flashcard as flashcard_route
import routes.quiz as quiz_route
import routes.study_guide as study_guide_route
from backend.app.models import (
    Course,
    DocumentChunk,
    GeneratedOutput,
    Quiz,
    UploadedDocument,
)
from schemas.flashcard import FlashcardRequest
from schemas.quiz import QuizRequest
from schemas.study_guide import StudyGuideRequest
from services.flashcard import FlashcardService
from services.profile_knowledge import (
    PROFILE_CONTEXT_DIRECTIVE,
    PROFILE_CONTEXT_HEADER,
)
from services.prompt_components import SHARED_SAFETY_DIRECTIVE
from services.quiz import QuizService
from services.study_guide import StudyGuideService
from services.text_generation import GenerationMetadata

STUB_METADATA = GenerationMetadata(provider="ollama", model="qwen3:8b", latency_ms=5)


def ascending_seeds(count: int) -> list[float]:
    """Rank chunks in corpus order so budget-bound selection stays deterministic."""
    return [index * 0.1 for index in range(count)]


def seed_ready_material(
    session,
    course_id: int,
    texts,
    *,
    file_hash: str,
    retrieval_env,
    seeds: list[float] | None = None,
) -> UploadedDocument:
    course = session.get(Course, course_id)
    assert course is not None
    document = UploadedDocument(
        original_file_name=f"{file_hash[:6]}.txt",
        file_type="txt",
        mime_type="text/plain",
        file_size=10,
        file_hash=file_hash,
        user_id=course.owner_id,
        course=course,
        storage_provider="local:test",
        storage_key=f"{file_hash[:6]}.txt",
        status="ready",
    )
    session.add(document)
    session.flush()
    chunks = [
        DocumentChunk(
            document=document,
            course=course,
            chunk_index=index,
            page_number=None,
            text=text,
        )
        for index, text in enumerate(texts)
    ]
    session.add_all(chunks)
    session.flush()
    retrieval_env.index(
        session,
        document,
        chunks,
        seeds=seeds if seeds is not None else ascending_seeds(len(chunks)),
    )
    session.commit()
    return document


class RecordingProvider:
    """Captures every rendered prompt so a test can inspect what reached the model."""

    def __init__(self, result: dict[str, Any], error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.calls = 0
        self.prompt = ""
        self.prompts: list[str] = []

    def _record(self, prompt: str) -> None:
        self.calls += 1
        self.prompt = prompt
        self.prompts.append(prompt)
        if self._error is not None:
            raise self._error

    def generate_json_with_metadata(self, prompt: str):
        self._record(prompt)
        return self._result, STUB_METADATA

    def generate_json(self, prompt: str):
        self._record(prompt)
        return self._result


def install_provider(
    monkeypatch, feature: "GenerationFeature", provider: RecordingProvider
) -> RecordingProvider:
    monkeypatch.setattr(
        feature.route_module,
        "get_text_generation_provider",
        lambda *args, **kwargs: provider,
    )
    return provider


def quiz_payload() -> dict[str, Any]:
    return {
        "title": "Example Quiz",
        "questions": [
            {
                "question_number": index,
                "question_type": "multiple_choice",
                "topic": f"Topic {index}",
                "question": f"Question {index}?",
                "difficulty": "medium",
                "explanation": "Because the material says so.",
                "options": ["Option A", "Option B", "Option C", "Option D"],
                "correct_option_index": 0,
            }
            for index in range(1, 3)
        ],
    }


def study_guide_payload() -> dict[str, Any]:
    return {
        "title": "Example Guide",
        "summary": "Example summary",
        "key_points": [],
        "important_terms": [],
        "common_mistakes": [],
        "exam_tips": {"lecture_based": [], "ai_suggestions": []},
        "difficulty": {"level": "Easy", "reason": "Introductory material"},
        "estimated_study_time": "20 minutes",
        "prerequisites": [],
        "learning_objectives": [],
        "coverage": {"status": "Complete", "estimated_completeness": 100},
        "confidence_notes": "",
    }


def flashcard_payload() -> dict[str, Any]:
    return {
        "deck_title": "Example Flashcards",
        "card_count": 10,
        "flashcards": [
            {
                "card_number": index,
                "difficulty": (
                    "Easy" if index <= 3 else "Medium" if index <= 7 else "Hard"
                ),
                "front": f"Question {index}?",
                "back": f"Answer {index}.",
            }
            for index in range(1, 11)
        ],
    }


def _quiz_request(*, use_profile_knowledge: bool) -> QuizRequest:
    return QuizRequest(
        question_count=2,
        question_types=["multiple_choice"],
        difficulty="medium",
        topic_focus="All Topics",
        use_profile_knowledge=use_profile_knowledge,
    )


def _study_guide_request(*, use_profile_knowledge: bool) -> StudyGuideRequest:
    return StudyGuideRequest(
        summary_format="comprehensive",
        topic_focus="All Topics",
        use_profile_knowledge=use_profile_knowledge,
    )


def _flashcard_request(*, use_profile_knowledge: bool) -> FlashcardRequest:
    return FlashcardRequest(
        topic_focus="All Topics",
        use_profile_knowledge=use_profile_knowledge,
    )


def _quiz_generate(db, course_id, request, provider, user_id):
    return QuizService.generate(db, course_id, request, provider, user_id=user_id)


def _study_guide_generate(db, course_id, request, provider, user_id):
    return StudyGuideService.generate(db, course_id, request, provider, user_id=user_id)


def _flashcard_generate(db, course_id, request, provider, user_id):
    return FlashcardService.generate(db, course_id, provider, request, user_id)


@dataclass(frozen=True)
class GenerationFeature:
    """One generation feature behind a signature-normalised interface."""

    name: str
    endpoint_suffix: str
    output_type: str
    route_module: ModuleType
    base_body: dict[str, Any]
    provider_payload: Callable[[], dict[str, Any]]
    build_request: Callable[..., Any]
    service_generate: Callable[..., Any]

    def __str__(self) -> str:
        return self.name

    def api_body(self, *, use_profile_knowledge: bool) -> dict[str, Any]:
        return {**self.base_body, "use_profile_knowledge": use_profile_knowledge}

    def endpoint(self, course_id: int) -> str:
        return f"/api/courses/{course_id}/{self.endpoint_suffix}"


GENERATION_FEATURES: tuple[GenerationFeature, ...] = (
    GenerationFeature(
        name="quiz",
        endpoint_suffix="quiz",
        output_type="quiz",
        route_module=quiz_route,
        base_body={
            "question_count": 2,
            "question_types": ["multiple_choice"],
            "difficulty": "medium",
            "topic_focus": "All Topics",
        },
        provider_payload=quiz_payload,
        build_request=_quiz_request,
        service_generate=_quiz_generate,
    ),
    GenerationFeature(
        name="study_guide",
        endpoint_suffix="study-guide",
        output_type="study_guide",
        route_module=study_guide_route,
        base_body={
            "summary_format": "comprehensive",
            "topic_focus": "All Topics",
        },
        provider_payload=study_guide_payload,
        build_request=_study_guide_request,
        service_generate=_study_guide_generate,
    ),
    GenerationFeature(
        name="flashcards",
        endpoint_suffix="flashcards",
        output_type="flashcards",
        route_module=flashcard_route,
        base_body={"topic_focus": "All Topics"},
        provider_payload=flashcard_payload,
        build_request=_flashcard_request,
        service_generate=_flashcard_generate,
    ),
)


def persisted_outputs(session_factory, course_id: int, output_type: str):
    with session_factory() as session:
        return session.scalars(
            select(GeneratedOutput)
            .where(GeneratedOutput.course_id == course_id)
            .where(GeneratedOutput.output_type == output_type)
        ).all()


def persisted_quizzes(session_factory, course_id: int):
    with session_factory() as session:
        return session.scalars(select(Quiz).where(Quiz.course_id == course_id)).all()


def template_body(prompt: str) -> str:
    """The rendered template alone, without the governance block appended after it.

    `PromptLoader.render` ends every prompt with the shared safety and grounding
    directives, which belong to no template section and would otherwise be
    counted as whatever section happens to come last.
    """
    start = prompt.find(SHARED_SAFETY_DIRECTIVE)
    return prompt if start == -1 else prompt[:start].rstrip("\n")


def profile_block(prompt: str) -> str:
    """The rendered supplementary block, which the templates always place last."""
    body = template_body(prompt)
    start = body.find(PROFILE_CONTEXT_HEADER)
    return "" if start == -1 else body[start:]


def profile_text_in_prompt(prompt: str) -> str:
    """Exactly the profile knowledge text the provider received, delimiters stripped."""
    block = profile_block(prompt)
    if not block:
        return ""
    marker = f"{PROFILE_CONTEXT_DIRECTIVE}\n\n"
    return block[block.index(marker) + len(marker) :]


def course_material_region(prompt: str) -> str:
    """Everything the model sees before the supplementary block begins.

    Trailing blank lines are not part of that text and are dropped: an empty
    supplementary block leaves the separator that preceded it at the end of the
    body, where the loader strips it before appending the governance block.
    """
    body = template_body(prompt)
    start = body.find(PROFILE_CONTEXT_HEADER)
    return (body if start == -1 else body[:start]).rstrip()
