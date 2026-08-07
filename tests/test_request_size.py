import anyio
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.app.request_size import RequestSizeLimitMiddleware


def test_upload_body_is_rejected_before_route_parsing() -> None:
    app = FastAPI()
    app.add_middleware(
        RequestSizeLimitMiddleware,
        max_request_body_size=4,
        max_upload_body_size=8,
    )

    @app.post("/api/courses/{course_id}/documents")
    async def consume_body(course_id: int, request: Request) -> dict[str, int]:
        return {"course_id": course_id, "size": len(await request.body())}

    with TestClient(app) as client:
        response = client.post(
            "/api/courses/1/documents",
            content=b"123456789",
        )

    assert response.status_code == 413
    assert response.json()["data"] == {"code": "UPLOAD_FILE_TOO_LARGE"}


def test_chunked_upload_without_content_length_is_limited() -> None:
    async def run_request() -> list[dict]:
        messages = iter(
            [
                {"type": "http.request", "body": b"12345", "more_body": True},
                {"type": "http.request", "body": b"6789", "more_body": False},
            ]
        )
        sent: list[dict] = []

        async def receive() -> dict:
            return next(messages)

        async def send(message: dict) -> None:
            sent.append(message)

        async def consume_body(scope, receive, send) -> None:
            while True:
                message = await receive()
                if not message.get("more_body", False):
                    break

        middleware = RequestSizeLimitMiddleware(
            consume_body,
            max_request_body_size=4,
            max_upload_body_size=8,
        )
        await middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/courses/1/documents",
                "headers": [],
            },
            receive,
            send,
        )
        return sent

    sent = anyio.run(run_request)

    response_start = next(
        message for message in sent if message["type"] == "http.response.start"
    )
    assert response_start["status"] == 413


def test_non_upload_request_body_uses_general_limit() -> None:
    app = FastAPI()
    app.add_middleware(
        RequestSizeLimitMiddleware,
        max_request_body_size=8,
        max_upload_body_size=100,
    )

    @app.post("/register")
    async def consume_body(request: Request) -> dict[str, int]:
        return {"size": len(await request.body())}

    with TestClient(app) as client:
        response = client.post("/register", content=b"123456789")

    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large"}
