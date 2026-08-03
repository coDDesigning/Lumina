"""ASGI request-size enforcement before framework body parsing."""

import tempfile
from typing import BinaryIO

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MULTIPART_OVERHEAD_BYTES = 1024 * 1024
REPLAY_CHUNK_SIZE = 1024 * 1024


class RequestSizeLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        max_request_body_size: int,
        max_upload_body_size: int,
    ) -> None:
        self.app = app
        self.max_request_body_size = max_request_body_size
        self.max_upload_body_size = max_upload_body_size

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        is_upload = self._is_document_upload(scope)
        limit = self.max_upload_body_size if is_upload else self.max_request_body_size
        content_length = self._content_length(scope)
        if content_length is not None:
            if content_length > limit:
                await self._send_too_large(scope, receive, send, is_upload)
                return
            await self.app(scope, receive, send)
            return

        with tempfile.SpooledTemporaryFile(max_size=REPLAY_CHUNK_SIZE) as buffered:
            if not await self._buffer_body(receive, buffered, limit):
                await self._send_too_large(scope, receive, send, is_upload)
                return
            buffered.seek(0)
            await self.app(scope, self._replay_receive(buffered), send)

    @staticmethod
    async def _buffer_body(receive: Receive, buffered: BinaryIO, limit: int) -> bool:
        received_bytes = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return True
            if message["type"] != "http.request":
                continue

            body = message.get("body", b"")
            received_bytes += len(body)
            if received_bytes > limit:
                return False
            buffered.write(body)
            if not message.get("more_body", False):
                return True

    @staticmethod
    def _replay_receive(buffered: BinaryIO) -> Receive:
        async def receive() -> Message:
            chunk = buffered.read(REPLAY_CHUNK_SIZE)
            return {
                "type": "http.request",
                "body": chunk,
                "more_body": bool(chunk),
            }

        return receive

    @staticmethod
    def _is_document_upload(scope: Scope) -> bool:
        path = scope.get("path", "")
        return (
            scope.get("method") == "POST"
            and path.startswith("/api/courses/")
            and path.endswith("/documents")
        )

    @staticmethod
    def _content_length(scope: Scope) -> int | None:
        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    return int(value)
                except ValueError:
                    return None
        return None

    @staticmethod
    async def _send_too_large(
        scope: Scope,
        receive: Receive,
        send: Send,
        is_upload: bool,
    ) -> None:
        content = (
            {
                "success": False,
                "message": "The uploaded file exceeds the configured size limit.",
                "data": {"code": "UPLOAD_FILE_TOO_LARGE"},
            }
            if is_upload
            else {"detail": "Request body too large"}
        )
        await JSONResponse(status_code=413, content=content)(scope, receive, send)
