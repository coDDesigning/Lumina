from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from schemas.auth import TokenData
from utils.security import SECRET_KEY, ALGORITHM

# OAuth2 şeması, swagger UI ve uygulamanın token'ı nereden alacağını belirtir
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login")

async def get_current_user_token(token: str = Depends(oauth2_scheme)) -> TokenData:
    """
    Gelen requestteki JWT token'ı doğrular ve içindeki kullanıcı bilgilerini (TokenData) çıkarır.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub") # Token oluştururken email'i 'sub' (subject) alanına koyacağız
        if email is None:
            raise credentials_exception
        token_data = TokenData(email=email)
    except JWTError:
        raise credentials_exception
    
    # Normalde burada veritabanından kullanıcı nesnesi çekilip döndürülür:
    # user = db.query(User).filter(User.email == token_data.email).first()
    # if user is None: raise credentials_exception
    # return user
    
    return token_data
