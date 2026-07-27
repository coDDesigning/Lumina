from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from schemas.auth import UserCreate, UserLogin, Token, TokenData
from utils.security import get_password_hash, create_access_token
from utils.deps import get_current_user_token

router = APIRouter(prefix="/api/auth", tags=["Authentication"])

@router.post("/register")
async def register_user(user: UserCreate):
    """
    Handles user registration. Hashes password and prepares it for the database service.
    """
    hashed_pw = get_password_hash(user.password)
    
    return {
        "message": "User registered successfully (Mock)",
        "user_email": user.email,
        "hashed_password_preview": hashed_pw[:15] + "..." 
    }

@router.post("/login", response_model=Token)
async def login_user(form_data: OAuth2PasswordRequestForm = Depends()):
    """
    Handles user login and returns a JWT Access Token. 
    Note: OAuth2 standard expects 'username'. We use email as the username.
    """
    # TODO: Query user from DB by form_data.username and verify password
    # If not matched, raise HTTPException(400, "Incorrect email or password")
    
    # MOCK: Accept any credentials and create a token for now
    access_token = create_access_token(data={"sub": form_data.username})
    
    return {"access_token": access_token, "token_type": "bearer"}

@router.get("/me")
async def read_users_me(current_user: TokenData = Depends(get_current_user_token)):
    """
    Protected endpoint to test token verification.
    Requires a valid JWT Bearer token to access.
    """
    return {
        "message": "You have access! Token is valid.",
        "user": current_user
    }