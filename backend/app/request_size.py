"""ASGI request-size enforcement before framework body parsing."""

import asyncio
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
        max_concurrent_uploads: int,
        upload_request_timeout_seconds: float,
    ) -> None:
        if max_concurrent_uploads <= 0:
            raise ValueError("max_concurrent_uploads must be positive")
        if upload_request_timeout_seconds <= 0:
            raise ValueError("upload_request_timeout_seconds must be positive")
        self.app = app
        self.max_request_body_size = max_request_body_size
        self.max_upload_body_size = max_upload_body_size
        self.upload_request_timeout_seconds = upload_request_timeout_seconds
        self._upload_slots = asyncio.Semaphore(max_concurrent_uploads)

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if self._has_ambiguous_framing(scope):
            await self._send_invalid_framing(scope, receive, send)
            return

        is_upload = self._is_document_upload(scope)
        if is_upload:
            await self._handle_upload(scope, receive, send)
            return

        await self._handle_request(scope, receive, send, is_upload=False)

    async def _handle_upload(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        deadline = (
            asyncio.get_running_loop().time() + self.upload_request_timeout_seconds
        )
        try:
            async with asyncio.timeout_at(deadline):
                await self._upload_slots.acquire()
        except TimeoutError:
            await self._send_request_timeout(scope, receive, send)
            return

        response_messages: list[Message] = []
        body_timed_out = False

        async def receive_before_deadline() -> Message:
            nonlocal body_timed_out
            try:
                async with asyncio.timeout_at(deadline):
                    return await receive()
            except TimeoutError as exc:
                body_timed_out = True
                raise RequestBodyTimeout from exc

        async def capture_response(message: Message) -> None:
            response_messages.append(message)

        try:
            await self._handle_request(
                scope,
                receive_before_deadline,
                capture_response,
                is_upload=True,
            )
        except RequestBodyTimeout:
            body_timed_out = True
        finally:
            self._upload_slots.release()

        if body_timed_out:
            await self._send_request_timeout(scope, receive, send)
            return

        for message in response_messages:
            await send(message)

    async def _handle_request(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        is_upload: bool,
    ) -> None:
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
    def _has_ambiguous_framing(scope: Scope) -> bool:
        header_names = {name.lower() for name, _value in scope.get("headers", [])}
        return (
            b"content-length" in header_names and b"transfer-encoding" in header_names
        )

    @staticmethod
    async def _send_invalid_framing(
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        await JSONResponse(
            status_code=400,
            content={"detail": "Ambiguous request framing"},
            headers={"Connection": "close"},
        )(scope, receive, send)

    @staticmethod
    async def _send_request_timeout(
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        await JSONResponse(
            status_code=408,
            content={"detail": "Upload request timed out"},
            headers={"Connection": "close"},
        )(scope, receive, send)

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
        await JSONResponse(
            status_code=413,
            content=content,
            headers={"Connection": "close"},
        )(scope, receive, send)


class RequestBodyTimeout(TimeoutError):
    """The upload body was not received before its admission deadline."""
