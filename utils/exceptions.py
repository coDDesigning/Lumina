from fastapi import HTTPException, status

ERROR_CODE_HEADER = "X-Error-Code"


class NotFoundException(HTTPException):
    def __init__(
        self, detail: str = "Item not found", *, error_code: str = "not_found"
    ):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=detail,
            headers={ERROR_CODE_HEADER: error_code},
        )


class BadRequestException(HTTPException):
    def __init__(self, detail: str = "Bad request", *, error_code: str = "bad_request"):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
            headers={ERROR_CODE_HEADER: error_code},
        )


class ConflictException(HTTPException):
    def __init__(self, detail: str = "Conflict", *, error_code: str = "conflict"):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail=detail,
            headers={ERROR_CODE_HEADER: error_code},
        )


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
                ERROR_CODE_HEADER: error_code,
            },
        )
