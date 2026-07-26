from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from schemas.auth import UserCreate, UserLogin, Token, TokenData
from utils.security import get_password_hash, create_access_token
from utils.deps import get_current_user_token

router = APIRouter(prefix="/api/auth", tags=["Authentication"])

@router.post("/register")
async def register_user(user: UserCreate):
    """
    Kullanıcı kayıt işlemlerini karşılar. Şifreyi hash'leyip veritabanı servisine iletmeye hazır hale getirir.
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
    Kullanıcı giriş işlemlerini karşılar ve doğrulama sonrası JWT Access Token döndürür.
    Not: OAuth2 standardı gereği login formunda 'username' ve 'password' alanları gelir. 
    Biz email adresini 'username' alanından alıyoruz.
    """
    # NORMALDE: Veritabanından kullanıcıyı form_data.username ile buluruz.
    # Sonra utils.security.verify_password ile şifresini kontrol ederiz.
    # Eğer eşleşmezse HTTPException(400, "Incorrect email or password") fırlatırız.
    
    # ŞİMDİLİK: Her isteği başarılı kabul edip mock bir token üretiyoruz
    access_token = create_access_token(data={"sub": form_data.username})
    
    return {"access_token": access_token, "token_type": "bearer"}

@router.get("/me")
async def read_users_me(current_user: TokenData = Depends(get_current_user_token)):
    """
    Token doğrulamasını test etmek için korumalı (protected) bir endpoint.
    Sadece geçerli bir JWT (Bearer) token gönderenler buraya erişebilir.
    """
    return {
        "message": "You have access! Token is valid.",
        "user": current_user
    }