from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from backend.app.database import get_db
from schemas.auth import Token
from schemas.user import UserCreate, UserResponse
from services.user import UserService
from utils.deps import get_current_user
from utils.security import create_access_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


@router.post("/register")
def register_user(
    user: UserCreate,
    db: Annotated[Session, Depends(get_db)],
    bootstrap_token: Annotated[str | None, Header(alias="X-Bootstrap-Token")] = None,
):
    """
    Handles user registration. Hashes password and prepares it for the database service.
    """
    created_user = UserService.create_user(db, user, bootstrap_token)

    return {
        "message": "User registered successfully",
        "user_email": created_user.email,
        "role": created_user.role.name,
    }


@router.post("/login", response_model=Token)
def login_user(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Handles user login and returns a JWT Access Token.
    Note: OAuth2 standard expects 'username'. We use email as the username.
    """
    user = UserService.get_user_by_email(db, form_data.username)
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if user.is_banned:
        raise HTTPException(status_code=403, detail="Your account has been banned.")

    access_token = create_access_token(data={"sub": user.email})

    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/me", response_model=UserResponse)
def read_users_me(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
):
    """
    Protected endpoint to test token verification.
    Requires a valid JWT Bearer token to access.
    """
    return current_user
