from scripts.check_legal_artifacts import (
    PROJECT_ROOT,
    browser_runtime_dependencies,
    main,
    python_runtime_dependencies,
)
from backend.app.legal import (
    POLICY_EFFECTIVE_DATE,
    PRIVACY_POLICY_VERSION,
    TERMS_POLICY_VERSION,
)


def test_every_direct_runtime_dependency_has_a_notice() -> None:
    notices = (
        (PROJECT_ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8").lower()
    )
    dependencies = python_runtime_dependencies() | browser_runtime_dependencies()

    assert dependencies - {"react-dom", "psycopg-binary"} <= {
        token.strip("`")
        for token in notices.split()
        if token.startswith("`") and token.endswith("`")
    }


def test_legal_artifacts_are_complete_and_distributed() -> None:
    assert main() == 0


def test_frontend_and_backend_publish_the_same_policy_revision() -> None:
    source = (
        PROJECT_ROOT / "frontend" / "src" / "features" / "legal" / "legalDocuments.tsx"
    ).read_text(encoding="utf-8")

    assert f"export const POLICY_VERSION = '{TERMS_POLICY_VERSION}'" in source
    assert TERMS_POLICY_VERSION == PRIVACY_POLICY_VERSION
    assert "export const POLICY_EFFECTIVE_DATE = '12 September 2026'" in source
    assert POLICY_EFFECTIVE_DATE.isoformat() == "2026-09-12"
