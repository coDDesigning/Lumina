from datetime import datetime
import sqlalchemy as sa
from sqlalchemy.orm import Session

from backend.app.models import RevokedToken


class TokenRevocationService:
    @staticmethod
    def revoke_token(
        db: Session, jti: str, expires_at: datetime, user_id: int | None = None
    ) -> None:
        """Adds a token's JTI to the denylist."""
        token = RevokedToken(jti=jti, expires_at=expires_at, user_id=user_id)
        db.add(token)
        try:
            db.flush()
        except sa.exc.IntegrityError:
            db.rollback()

    @staticmethod
    def is_token_revoked(db: Session, jti: str) -> bool:
        """Checks if a given JTI is in the denylist."""
        return db.scalar(sa.select(sa.exists().where(RevokedToken.jti == jti)))
