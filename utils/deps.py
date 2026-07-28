from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from schemas.auth import TokenData
from schemas.user import UserResponse, Role
from services.user import UserService
from utils.security import SECRET_KEY, ALGORITHM

# Defines the OAuth2 scheme and token URL for Swagger UI
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login")

async def get_current_user(token: str = Depends(oauth2_scheme)) -> UserResponse:
    """
    Validates the JWT token, extracts user, and checks if banned.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub") # Extract email from 'sub' (subject) claim
        if email is None:
            raise credentials_exception
        token_data = TokenData(email=email)
    except JWTError:
        raise credentials_exception
    
    # Fetch user object from the database here
    user_dict = UserService.get_user_by_email(token_data.email)
    if user_dict is None:
        raise credentials_exception
        
    if user_dict.get("is_banned"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Your account has been banned.")
        
    return UserResponse(**user_dict)

async def get_current_admin(current_user: UserResponse = Depends(get_current_user)) -> UserResponse:
    """
    Checks if the current user has the ADMIN role.
    """
    if current_user.role != Role.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not enough privileges. Admin access required.")
    return current_user
