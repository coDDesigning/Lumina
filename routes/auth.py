from fastapi import APIRouter
from schemas.auth import UserCreate, UserLogin
from utils.security import get_password_hash

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

@router.post("/login")
async def login_user(user: UserLogin):
    """
    Kullanıcı giriş işlemlerini karşılar.
    """
    return {"message": f"Login request received for {user.email}"}