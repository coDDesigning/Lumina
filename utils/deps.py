from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy.orm import Session

from backend.app.database import get_db
from datetime import datetime, timezone
from schemas.user import Role, UserResponse
from services.user import UserService
from services.token_revocation import TokenRevocationService
from utils.security import decode_access_token

# Defines the OAuth2 scheme and token URL for Swagger UI
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login")


def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> UserResponse:
    """
    Validates the JWT token, extracts user, and checks if banned.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
        subject = payload.get("sub")
        if not isinstance(subject, str) or not subject:
            raise credentials_exception
    except JWTError:
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
            if iat_dt < user.tokens_valid_after.replace(tzinfo=timezone.utc):
                raise credentials_exception
        except (ValueError, TypeError):
            pass

    return UserService.to_response(user)


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
        )
    return current_user
