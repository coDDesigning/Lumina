from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from schemas.auth import TokenData
from utils.security import SECRET_KEY, ALGORITHM

# Defines the OAuth2 scheme and token URL for Swagger UI
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login")

async def get_current_user_token(token: str = Depends(oauth2_scheme)) -> TokenData:
    """
    Validates the JWT token from the request and extracts user data.
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
    
    # TODO: Fetch user object from the database here
    # user = db.query(User).filter(User.email == token_data.email).first()
    # if user is None: raise credentials_exception
    # return user
    
    return token_data
