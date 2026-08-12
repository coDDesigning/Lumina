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
        max_concurrent_uploads=1,
        upload_request_timeout_seconds=1,
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
    assert response.headers["connection"] == "close"


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
            max_concurrent_uploads=1,
            upload_request_timeout_seconds=1,
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
        max_concurrent_uploads=1,
        upload_request_timeout_seconds=1,
    )

    @app.post("/register")
    async def consume_body(request: Request) -> dict[str, int]:
        return {"size": len(await request.body())}

    with TestClient(app) as client:
        response = client.post("/register", content=b"123456789")

    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large"}


def test_upload_concurrency_is_limited_before_body_consumption() -> None:
    async def run_requests() -> None:
        first_started = anyio.Event()
        release_first = anyio.Event()
        second_consumed = anyio.Event()

        async def consume_body(scope, receive, send) -> None:
            message = await receive()
            if message["body"] == b"first":
                first_started.set()
                await release_first.wait()
            else:
                second_consumed.set()

        middleware = RequestSizeLimitMiddleware(
            consume_body,
            max_request_body_size=4,
            max_upload_body_size=8,
            max_concurrent_uploads=1,
            upload_request_timeout_seconds=1,
        )
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/courses/1/documents",
            "headers": [(b"content-length", b"5")],
        }

        def receive(body: bytes):
            async def receive_message() -> dict:
                return {"type": "http.request", "body": body, "more_body": False}

            return receive_message

        async def send(_message: dict) -> None:
            return None

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(middleware, scope, receive(b"first"), send)
            await first_started.wait()
            task_group.start_soon(middleware, scope, receive(b"other"), send)
            with anyio.move_on_after(0.05) as second_started_early:
                await second_consumed.wait()
            assert second_started_early.cancel_called
            release_first.set()

        assert second_consumed.is_set()

    anyio.run(run_requests)


def test_ambiguous_upload_framing_is_rejected() -> None:
    async def run_request() -> list[dict]:
        sent: list[dict] = []
        app_called = False

        async def receive() -> dict:
            return {"type": "http.request", "body": b"oversized", "more_body": False}

        async def send(message: dict) -> None:
            sent.append(message)

        async def consume_body(scope, receive, send) -> None:
            nonlocal app_called
            app_called = True

        middleware = RequestSizeLimitMiddleware(
            consume_body,
            max_request_body_size=4,
            max_upload_body_size=8,
            max_concurrent_uploads=1,
            upload_request_timeout_seconds=1,
        )
        await middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/courses/1/documents",
                "headers": [
                    (b"content-length", b"1"),
                    (b"transfer-encoding", b"chunked"),
                ],
            },
            receive,
            send,
        )
        assert app_called is False
        return sent

    sent = anyio.run(run_request)

    response_start = next(
        message for message in sent if message["type"] == "http.response.start"
    )
    assert response_start["status"] == 400
    assert (b"connection", b"close") in response_start["headers"]


def test_upload_body_times_out_before_application_parsing() -> None:
    async def run_request() -> list[dict]:
        sent: list[dict] = []

        async def receive() -> dict:
            await anyio.sleep_forever()
            raise AssertionError("unreachable")

        async def send(message: dict) -> None:
            sent.append(message)

        async def consume_body(scope, receive, send) -> None:
            try:
                await receive()
            except TimeoutError:
                await send(
                    {
                        "type": "http.response.start",
                        "status": 400,
                        "headers": [],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": b"caught by application",
                    }
                )

        middleware = RequestSizeLimitMiddleware(
            consume_body,
            max_request_body_size=4,
            max_upload_body_size=8,
            max_concurrent_uploads=1,
            upload_request_timeout_seconds=0.01,
        )
        await middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/courses/1/documents",
                "headers": [(b"content-length", b"5")],
            },
            receive,
            send,
        )
        return sent

    sent = anyio.run(run_request)

    response_start = next(
        message for message in sent if message["type"] == "http.response.start"
    )
    assert response_start["status"] == 408
    assert (b"connection", b"close") in response_start["headers"]
    response_bodies = [
        message["body"] for message in sent if message["type"] == "http.response.body"
    ]
    assert response_bodies == [b'{"detail":"Upload request timed out"}']


def test_upload_admission_times_out_before_body_consumption() -> None:
    async def run_requests() -> list[dict]:
        first_started = anyio.Event()
        release_first = anyio.Event()
        second_consumed = False
        second_response: list[dict] = []

        async def consume_body(scope, receive, send) -> None:
            message = await receive()
            if message["body"] == b"first":
                first_started.set()
                await release_first.wait()

        middleware = RequestSizeLimitMiddleware(
            consume_body,
            max_request_body_size=4,
            max_upload_body_size=8,
            max_concurrent_uploads=1,
            upload_request_timeout_seconds=0.01,
        )
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/courses/1/documents",
            "headers": [(b"content-length", b"5")],
        }

        async def receive_first() -> dict:
            return {"type": "http.request", "body": b"first", "more_body": False}

        async def receive_second() -> dict:
            nonlocal second_consumed
            second_consumed = True
            return {"type": "http.request", "body": b"other", "more_body": False}

        async def discard(_message: dict) -> None:
            return None

        async def send_second(message: dict) -> None:
            second_response.append(message)

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(middleware, scope, receive_first, discard)
            await first_started.wait()
            task_group.start_soon(middleware, scope, receive_second, send_second)
            await anyio.sleep(0.02)
            release_first.set()

        assert second_consumed is False
        return second_response

    sent = anyio.run(run_requests)

    response_start = next(
        message for message in sent if message["type"] == "http.response.start"
    )
    assert response_start["status"] == 408
    assert (b"connection", b"close") in response_start["headers"]
