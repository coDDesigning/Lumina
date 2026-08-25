from fastapi import HTTPException, status


class NotFoundException(HTTPException):
    def __init__(self, detail: str = "Item not found"):
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


class BadRequestException(HTTPException):
    def __init__(self, detail: str = "Bad request"):
        super().__init__(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


class ConflictException(HTTPException):
    def __init__(self, detail: str = "Conflict"):
        super().__init__(status_code=status.HTTP_409_CONFLICT, detail=detail)


class TooManyRequestsException(HTTPException):
    """Raised when a rate limit or lockout rejects a request.

    Carries ``Retry-After`` and the same ``X-Error-Code`` header convention
    AI routes use, so a client can distinguish an abuse-control rejection
    from an AI-provider rate limit without parsing ``detail`` text.
    """

    def __init__(
        self,
        detail: str = "Too many requests",
        *,
        retry_after_seconds: int,
        error_code: str = "rate_limited",
    ):
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=detail,
            headers={
                "Retry-After": str(retry_after_seconds),
                "X-Error-Code": error_code,
            },
        )
