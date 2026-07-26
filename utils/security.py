import bcrypt
from datetime import datetime, timedelta
from typing import Optional
from jose import jwt

# Bunlar normalde .env dosyasından okunmalıdır. Şimdilik burada tanımlıyoruz.
SECRET_KEY = "super-secret-lumina-key-for-development-only"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7 # 7 gün

def get_password_hash(password: str) -> str:
    """Düz metin şifreyi geri döndürülemez bir hash'e çevirir."""
    pwd_bytes = password.encode('utf-8')
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Kullanıcının girdiği şifre ile veritabanındaki hash eşleşiyor mu kontrol eder."""
    pwd_bytes = plain_password.encode('utf-8')
    hash_bytes = hashed_password.encode('utf-8')
    try:
        return bcrypt.checkpw(pwd_bytes, hash_bytes)
    except Exception:
        return False

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """JWT Access Token oluşturur."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt