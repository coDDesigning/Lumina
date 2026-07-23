from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def get_password_hash(password: str) -> str:
    """Düz metin şifreyi geri döndürülemez bir hash'e çevirir."""
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Kullanıcının girdiği şifre ile veritabanındaki hash eşleşiyor mu kontrol eder."""
    return pwd_context.verify(plain_password, hashed_password)