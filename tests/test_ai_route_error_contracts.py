"""Contract and AST invariant tests across every AI generation route.

Enforces:
1. AST hygiene: No route module catches subclasses alongside Exception (no dead handlers),
   and no route module imports or manipulates domain persistence (GeneratedOutput, Quiz).
2. Public error contract: Expected provider/retrieval failures return appropriate
   status codes with mandatory X-Error-Code headers.
3. Unexpected failures: Unhandled exceptions return 500 with X-Error-Code: generation_failed,
   mask internal details (prevent secret/host leaks), refund credits, and leave no
   partial domain rows behind.
"""

import ast
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.app.models import (
    Conversation,
    ConversationMessage,
    GeneratedOutput,
    Quiz,
    QuizQuestion,
    User,
)
from services.retrieval_material import (
    MaterialNotIndexedError,
    NoRelevantMaterialError,
)
from services.text_generation import (
    TextGenerationConnectionError,
    TextGenerationRateLimitError,
    TextGenerationTimeoutError,
)
from tests.conftest import set_balance
from tests.test_credits import _add_material
from utils.ai_errors import ERROR_CODE_HEADER, PUBLIC_MESSAGES, AiErrorCode

ROUTES_DIR = Path(__file__).resolve().parents[1] / "routes"
AI_ROUTE_FILES = [
    "exam_roadmap.py",
    "study_guide.py",
    "flashcard.py",
    "quiz.py",
    "course_qa.py",
    "ai_tutor.py",
    "prompt_generator.py",
]


# ---------------------------------------------------------------------------
# 1. AST & Route Boundary Invariants
# ---------------------------------------------------------------------------


def test_no_ai_route_catches_subclasses_beside_exception() -> None:
    """A try/except block must not list a specific subclass beside Exception.

    Catching (Subclass, Exception) leaves Subclass dead and masks intent. Routes must
    either catch specific classes or Exception cleanly.
    """
    for filename in AI_ROUTE_FILES:
        filepath = ROUTES_DIR / filename
        assert filepath.exists(), f"{filename} does not exist"
        tree = ast.parse(filepath.read_text(encoding="utf-8"), filename=str(filepath))

        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and isinstance(node.type, ast.Tuple):
                type_names = [
                    elt.id for elt in node.type.elts if isinstance(elt, ast.Name)
                ]
                if "Exception" in type_names:
                    pytest.fail(
                        f"{filename}:{node.lineno} catches Exception alongside "
                        f"specific classes: {type_names}. Remove redundant subclasses."
                    )


def test_quiz_route_does_not_import_generated_output_service_or_models() -> None:
    """routes/quiz.py must not directly manage GeneratedOutput or domain models.

    Persistence belongs strictly within QuizService with a single transaction boundary.
    """
    filepath = ROUTES_DIR / "quiz.py"
    tree = ast.parse(filepath.read_text(encoding="utf-8"), filename=str(filepath))

    forbidden_imports = {"GeneratedOutputService", "GeneratedOutput", "QuizQuestion"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                assert alias.name not in forbidden_imports, (
                    f"routes/quiz.py must not import {alias.name}; "
                    "persistence must be delegated to QuizService."
                )


# ---------------------------------------------------------------------------
# 2. Unexpected Failure Contracts (500 + X-Error-Code + Leak Prevention + Refund)
# ---------------------------------------------------------------------------

SYNTHETIC_LEAK = "host=db.prod.internal:5432 user=superuser secret=sk_live_xyz987"


AI_ENDPOINT_CONFIGS = [
    (
        "quiz",
        "services.quiz.QuizService.generate",
        {
            "question_count": 1,
            "question_types": ["multiple_choice"],
            "difficulty": "medium",
            "topic_focus": "All Topics",
        },
        True,  # course-scoped
    ),
    (
        "study-guide",
        "services.study_guide.StudyGuideService.generate",
        {"summary_format": "overview", "topic_focus": "Sorting"},
        True,
    ),
    (
        "flashcards",
        "services.flashcard.FlashcardService.generate",
        {"concept_count": 1, "topic_focus": "Sorting"},
        True,
    ),
    (
        "qa",
        "services.course_qa.CourseQAService.generate",
        {"question": "What is sorting?"},
        True,
    ),
    (
        "ai-tutor",
        "services.ai_tutor.AiTutorService.generate",
        {"question": "Explain sorting step by step."},
        True,
    ),
    (
        "prompt-generator",
        "services.prompt_generator.PromptGeneratorService.generate",
        {"description": "A quiz about sorting algorithms"},
        False,  # global / non-course-scoped
    ),
]


@pytest.mark.parametrize(
    ("endpoint", "service_target", "payload", "is_course_scoped"),
    AI_ENDPOINT_CONFIGS,
)
def test_unexpected_failure_returns_500_with_error_code_and_refunds(
    authz_api,
    retrieval_env,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
    service_target: str,
    payload: dict,
    is_course_scoped: bool,
) -> None:
    """Unexpected exceptions must return 500, set X-Error-Code: generation_failed,

    never leak raw exception strings, refund credits, and leave no database records.
    """
    if is_course_scoped:
        _add_material(
            authz_api.session_factory,
            authz_api.user_a_id,
            authz_api.a_course_id,
            retrieval_env,
        )
        url = f"/api/courses/{authz_api.a_course_id}/{endpoint}"
    else:
        url = f"/api/{endpoint}"

    set_balance(authz_api.session_factory, authz_api.user_a_id, 20.0)

    def raise_synthetic_error(*args, **kwargs):
        raise RuntimeError(f"Database crash: {SYNTHETIC_LEAK}")

    monkeypatch.setattr(service_target, raise_synthetic_error)

    response = authz_api.client.post(
        url,
        json=payload,
        headers=authz_api.authorization_a,
    )

    # 1. Status and header contract
    assert response.status_code == 500
    assert (
        response.headers.get(ERROR_CODE_HEADER) == AiErrorCode.GENERATION_FAILED.value
    )

    # 2. No leak of internal details / secrets
    assert "db.prod.internal" not in response.text
    assert "sk_live_xyz987" not in response.text
    assert response.json()["detail"] == PUBLIC_MESSAGES[AiErrorCode.GENERATION_FAILED]

    # 3. Credits refunded / unchanged
    with authz_api.session_factory() as session:
        user = session.get(User, authz_api.user_a_id)
        assert user.credits == 20.0

        # 4. No orphan records created
        assert session.scalars(select(Quiz)).all() == []
        assert session.scalars(select(QuizQuestion)).all() == []
        assert session.scalars(select(GeneratedOutput)).all() == []
        assert session.scalars(select(Conversation)).all() == []
        assert session.scalars(select(ConversationMessage)).all() == []


# ---------------------------------------------------------------------------
# 3. Expected Failure Contracts (Provider / Retrieval error mappings)
# ---------------------------------------------------------------------------

PROVIDER_FAILURE_CASES = [
    (
        TextGenerationTimeoutError("Timed out"),
        504,
        AiErrorCode.PROVIDER_TIMEOUT,
    ),
    (
        TextGenerationRateLimitError("Rate limited"),
        429,
        AiErrorCode.PROVIDER_RATE_LIMITED,
    ),
    (
        TextGenerationConnectionError("Connection refused"),
        503,
        AiErrorCode.PROVIDER_UNAVAILABLE,
    ),
]


@pytest.mark.parametrize(
    ("exception_to_raise", "expected_status", "expected_code"),
    PROVIDER_FAILURE_CASES,
)
@pytest.mark.parametrize(
    ("endpoint", "provider_dependency", "payload"),
    [
        (
            "study-guide",
            "routes.study_guide.get_text_generation_provider",
            {"summary_format": "overview", "topic_focus": "Sorting"},
        ),
        (
            "flashcards",
            "routes.flashcard.get_text_generation_provider",
            {"concept_count": 1, "topic_focus": "Sorting"},
        ),
        (
            "quiz",
            "routes.quiz.get_text_generation_provider",
            {
                "question_count": 1,
                "question_types": ["multiple_choice"],
                "difficulty": "medium",
                "topic_focus": "All Topics",
            },
        ),
        (
            "qa",
            "routes.course_qa.get_text_generation_provider",
            {"question": "What is sorting?"},
        ),
        (
            "ai-tutor",
            "routes.ai_tutor.get_text_generation_provider",
            {"question": "What is sorting?"},
        ),
    ],
)
def test_provider_failures_map_to_expected_contract(
    authz_api,
    retrieval_env,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
    provider_dependency: str,
    payload: dict,
    exception_to_raise: Exception,
    expected_status: int,
    expected_code: AiErrorCode,
) -> None:
    _add_material(
        authz_api.session_factory,
        authz_api.user_a_id,
        authz_api.a_course_id,
        retrieval_env,
    )
    set_balance(authz_api.session_factory, authz_api.user_a_id, 20.0)

    class FailingProvider:
        def generate_text(self, *args, **kwargs):
            raise exception_to_raise

        def generate_text_with_metadata(self, *args, **kwargs):
            raise exception_to_raise

        def generate_json(self, *args, **kwargs):
            raise exception_to_raise

        def generate_json_with_metadata(self, *args, **kwargs):
            raise exception_to_raise

    monkeypatch.setattr(provider_dependency, lambda **kwargs: FailingProvider())

    url = f"/api/courses/{authz_api.a_course_id}/{endpoint}"
    response = authz_api.client.post(
        url,
        json=payload,
        headers=authz_api.authorization_a,
    )

    assert response.status_code == expected_status
    assert response.headers.get(ERROR_CODE_HEADER) == expected_code.value
    assert response.json()["detail"] == PUBLIC_MESSAGES[expected_code]

    with authz_api.session_factory() as session:
        user = session.get(User, authz_api.user_a_id)
        assert user.credits == 20.0


RETRIEVAL_FAILURE_CASES = [
    (
        MaterialNotIndexedError("No embeddings indexed"),
        409,
        AiErrorCode.MATERIAL_NOT_INDEXED,
    ),
    (
        NoRelevantMaterialError("No chunk similarity"),
        409,
        AiErrorCode.NO_RELEVANT_MATERIAL,
    ),
]


@pytest.mark.parametrize(
    ("exception_to_raise", "expected_status", "expected_code"),
    RETRIEVAL_FAILURE_CASES,
)
@pytest.mark.parametrize(
    ("endpoint", "material_target", "payload"),
    [
        (
            "study-guide",
            "services.study_guide.StudyGuideService.get_course_material",
            {"summary_format": "overview", "topic_focus": "Sorting"},
        ),
        (
            "flashcards",
            "services.flashcard.FlashcardService.get_course_material",
            {"concept_count": 1, "topic_focus": "Sorting"},
        ),
        (
            "quiz",
            "services.quiz.QuizService.get_course_material",
            {
                "question_count": 1,
                "question_types": ["multiple_choice"],
                "difficulty": "medium",
                "topic_focus": "All Topics",
            },
        ),
        (
            "qa",
            "services.course_qa.CourseQAService.get_course_material",
            {"question": "What is sorting?"},
        ),
        (
            "ai-tutor",
            "services.ai_tutor.AiTutorService.get_course_material",
            {"question": "What is sorting?"},
        ),
    ],
)
def test_retrieval_failures_map_to_expected_contract(
    authz_api,
    retrieval_env,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
    material_target: str,
    payload: dict,
    exception_to_raise: Exception,
    expected_status: int,
    expected_code: AiErrorCode,
) -> None:
    _add_material(
        authz_api.session_factory,
        authz_api.user_a_id,
        authz_api.a_course_id,
        retrieval_env,
    )
    set_balance(authz_api.session_factory, authz_api.user_a_id, 20.0)

    def fail_retrieval(*args, **kwargs):
        raise exception_to_raise

    monkeypatch.setattr(material_target, fail_retrieval)

    url = f"/api/courses/{authz_api.a_course_id}/{endpoint}"
    response = authz_api.client.post(
        url,
        json=payload,
        headers=authz_api.authorization_a,
    )

    assert response.status_code == expected_status
    assert response.headers.get(ERROR_CODE_HEADER) == expected_code.value
    assert response.json()["detail"] == PUBLIC_MESSAGES[expected_code]

    with authz_api.session_factory() as session:
        user = session.get(User, authz_api.user_a_id)
        assert user.credits == 20.0
