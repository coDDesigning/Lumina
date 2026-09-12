import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jwt import PyJWTError
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.database import get_db
from backend.app.observability import bind_operation_context, reset_operation_context
from schemas.user import Role, UserResponse
from services.user import UserService
from services.token_revocation import TokenRevocationService
from utils.security import decode_access_token

# Defines the OAuth2 scheme and token URL for Swagger UI
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login")

logger = logging.getLogger(__name__)

AUTH_STATE_KEY = "lumina_auth_state"
USER_ID_KEY = "lumina_user_id"


def _authenticate(
    request: Request,
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> UserResponse:
    """
    Validates the JWT token, extracts user, and checks if banned.
    """
    setattr(request.state, AUTH_STATE_KEY, "rejected")
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer", "X-Error-Code": "invalid_credentials"},
    )
    try:
        payload = decode_access_token(token)
        subject = payload.get("sub")
        if not isinstance(subject, str) or not subject:
            raise credentials_exception
    except PyJWTError:
        raise credentials_exception

    user = UserService.get_user_by_email(db, subject)
    if user is None:
        raise credentials_exception

    if user.is_banned:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has been banned.",
            headers={"X-Error-Code": "account_banned"},
        )

    jti = payload.get("jti")
    if jti and TokenRevocationService.is_token_revoked(db, jti):
        raise credentials_exception

    iat = payload.get("iat")
    if user.tokens_valid_after and iat:
        try:
            iat_dt = datetime.fromtimestamp(iat, tz=timezone.utc)
        except (ValueError, TypeError, OverflowError, OSError):
            logger.warning(
                "Rejected a token whose issued-at timestamp could not be read",
                extra={
                    "event": "token_iat_unreadable",
                    "error_code": "invalid_credentials",
                    "user_id": user.id,
                },
            )
            raise credentials_exception
        if iat_dt < user.tokens_valid_after.replace(tzinfo=timezone.utc):
            raise credentials_exception

    setattr(request.state, AUTH_STATE_KEY, "authenticated")
    setattr(request.state, USER_ID_KEY, user.id)
    return UserService.to_response(user)


async def get_current_user(
    current_user: Annotated[UserResponse, Depends(_authenticate)],
) -> AsyncIterator[UserResponse]:
    token = bind_operation_context(user_id=current_user.id)
    try:
        yield current_user
    finally:
        reset_operation_context(token)


def get_current_admin(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
) -> UserResponse:
    """
    Checks if the current user has the ADMIN role.
    """
    if current_user.role != Role.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough privileges. Admin access required.",
            headers={"X-Error-Code": "admin_required"},
        )
    return current_user


def get_verified_user(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
) -> UserResponse:
    """Require a proven address before an account can enqueue provider-backed work."""
    if (
        settings.email_verification_required
        and current_user.role != Role.ADMIN
        and not current_user.is_email_verified
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Verify your email address before creating or processing documents.",
            headers={"X-Error-Code": "email_verification_required"},
        )
    return current_user
