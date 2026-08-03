from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from schemas.auth import Token
from schemas.user import UserCreate, UserResponse
from utils.security import create_access_token, verify_password
from utils.deps import get_current_user
from services.user import UserService

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


@router.post("/register")
async def register_user(user: UserCreate):
    """
    Handles user registration. Hashes password and prepares it for the database service.
    """
    existing_user = UserService.get_user_by_email(user.email)
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")

    created_user = UserService.create_user(user)

    return {
        "message": "User registered successfully",
        "user_email": created_user["email"],
        "role": created_user["role"],
    }


@router.post("/login", response_model=Token)
async def login_user(form_data: OAuth2PasswordRequestForm = Depends()):
    """
    Handles user login and returns a JWT Access Token.
    Note: OAuth2 standard expects 'username'. We use email as the username.
    """
    user_dict = UserService.get_user_by_email(form_data.username)
    if not user_dict or not verify_password(form_data.password, user_dict["password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if user_dict.get("is_banned"):
        raise HTTPException(status_code=403, detail="Your account has been banned.")

    access_token = create_access_token(data={"sub": user_dict["email"]})

    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/me", response_model=UserResponse)
async def read_users_me(current_user: UserResponse = Depends(get_current_user)):
    """
    Protected endpoint to test token verification.
    Requires a valid JWT Bearer token to access.
    """
    return current_user
