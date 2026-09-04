import json
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run_probe(script: str, *, allowed_origins: str | None) -> dict[str, object]:
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "development",
            "APP_DEBUG": "false",
            "DEPLOYMENT_MODE": "self_hosted",
            "DATABASE_URL": "sqlite:///./data/lumina.db",
            "STORAGE_BACKEND": "local",
            "VECTOR_BACKEND": "chroma",
            "MAX_REQUEST_SIZE_BYTES": "4",
            "OLLAMA_BASE_URL": "http://localhost:11434",
            "EMBEDDING_PROVIDER": "ollama",
            "CREDIT_METERING_ENABLED": "false",
            "PYTHONPATH": str(PROJECT_ROOT),
        }
    )
    if allowed_origins is None:
        environment.pop("CORS_ALLOWED_ORIGINS", None)
    else:
        environment["CORS_ALLOWED_ORIGINS"] = allowed_origins

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    result_line = next(
        line.removeprefix("CORS_RESULT=")
        for line in completed.stdout.splitlines()
        if line.startswith("CORS_RESULT=")
    )
    return json.loads(result_line)


def test_cors_is_disabled_when_allowed_origins_are_unset() -> None:
    result = _run_probe(
        """
import json
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)
simple = client.get("/health/live", headers={"Origin": "https://app.example.com"})
preflight = client.options(
    "/health/live",
    headers={
        "Origin": "https://app.example.com",
        "Access-Control-Request-Method": "GET",
    },
)
client.close()
print("CORS_RESULT=" + json.dumps({
    "simple_allow_origin": simple.headers.get("access-control-allow-origin"),
    "preflight_allow_origin": preflight.headers.get("access-control-allow-origin"),
}))
""",
        allowed_origins=None,
    )

    assert result == {
        "simple_allow_origin": None,
        "preflight_allow_origin": None,
    }


def test_configured_cors_policy_and_request_size_responses() -> None:
    result = _run_probe(
        """
import json
from fastapi import Response
from fastapi.testclient import TestClient
from main import app

@app.get("/cors-error-probe")
def cors_error_probe() -> Response:
    return Response(status_code=409, headers={"X-Error-Code": "probe_error"})

@app.post("/cors-size-probe")
def cors_size_probe() -> dict[str, str]:
    return {"status": "unexpected"}

@app.get("/cors-unhandled-probe")
def cors_unhandled_probe() -> None:
    raise RuntimeError("probe")

client = TestClient(app, raise_server_exceptions=False)
allowed = client.get("/health/live", headers={"Origin": "https://app.example.com"})
disallowed = client.get("/health/live", headers={"Origin": "https://other.example.com"})
preflight = client.options(
    "/health/live",
    headers={
        "Origin": "https://app.example.com",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Authorization, Content-Type",
    },
)
error = client.get(
    "/cors-error-probe",
    headers={"Origin": "https://app.example.com"},
)
oversized = client.post(
    "/cors-size-probe",
    content=b"12345",
    headers={"Origin": "https://app.example.com"},
)
unhandled = client.get(
    "/cors-unhandled-probe",
    headers={"Origin": "https://app.example.com"},
)
client.close()
print("CORS_RESULT=" + json.dumps({
    "allowed_origin": allowed.headers.get("access-control-allow-origin"),
    "disallowed_origin": disallowed.headers.get("access-control-allow-origin"),
    "preflight_status": preflight.status_code,
    "preflight_origin": preflight.headers.get("access-control-allow-origin"),
    "preflight_methods": preflight.headers.get("access-control-allow-methods"),
    "preflight_headers": preflight.headers.get("access-control-allow-headers"),
    "credentials_present": any(
        "access-control-allow-credentials" in response.headers
        for response in (allowed, preflight, error, oversized)
    ),
    "expose_headers": error.headers.get("access-control-expose-headers"),
    "error_code": error.headers.get("X-Error-Code"),
    "oversized_status": oversized.status_code,
    "oversized_origin": oversized.headers.get("access-control-allow-origin"),
    "unhandled_status": unhandled.status_code,
    "unhandled_origin": unhandled.headers.get("access-control-allow-origin"),
}))
""",
        allowed_origins="https://app.example.com",
    )

    assert result["allowed_origin"] == "https://app.example.com"
    assert result["disallowed_origin"] is None
    assert result["preflight_status"] == 200
    assert result["preflight_origin"] == "https://app.example.com"
    assert result["preflight_methods"] == "GET, POST, PUT, PATCH, DELETE"
    allowed_headers = {
        header.strip().lower() for header in str(result["preflight_headers"]).split(",")
    }
    assert {"authorization", "content-type"} <= allowed_headers
    assert result["credentials_present"] is False
    assert result["expose_headers"] == "Retry-After, X-Error-Code, X-Request-ID"
    assert result["error_code"] == "probe_error"
    assert result["oversized_status"] == 413
    assert result["oversized_origin"] == "https://app.example.com"
    assert result["unhandled_status"] == 500
    assert result["unhandled_origin"] == "https://app.example.com"
