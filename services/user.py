import secrets

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from backend.app.config import settings
from backend.app.models import Role as RoleModel
from backend.app.models import User
from schemas.user import Role, UserCreate, UserResponse, UserUpdate
from services.text_generation import get_available_models
from utils.exceptions import BadRequestException, NotFoundException
from utils.security import get_password_hash


class UserService:
    """Handles business logic and data access for users."""

    @staticmethod
    def to_response(user: User) -> UserResponse:
        return UserResponse(
            id=user.id,
            name=user.name,
            email=user.email,
            role=Role(user.role.name),
            is_banned=user.is_banned,
            credits=user.credits,
            preferred_model=user.preferred_model,
        )

    @staticmethod
    def _get_role(db: Session, role: Role) -> RoleModel:
        role_model = db.scalar(select(RoleModel).where(RoleModel.name == role.value))
        if role_model is None:
            raise RuntimeError("Required roles are missing; apply database migrations.")
        return role_model

    @staticmethod
    def get_user_by_email(db: Session, email: str) -> User | None:
        """Finds a user by their email address."""
        canonical_email = UserService.canonicalize_email(email)
        if canonical_email is None:
            return None
        return db.scalar(
            select(User)
            .options(selectinload(User.role))
            .where(User.email == canonical_email)
        )

    @staticmethod
    def canonicalize_email(email: str) -> str | None:
        try:
            return validate_email(
                email.strip(),
                check_deliverability=False,
            ).normalized.lower()
        except EmailNotValidError:
            return None

    @staticmethod
    def create_user(
        db: Session,
        user_data: UserCreate,
        bootstrap_token: str | None = None,
    ) -> User:
        """Register a user and assign the configured initial administrator."""
        if UserService.get_user_by_email(db, user_data.email) is not None:
            raise BadRequestException("Email already registered")

        user_count = db.scalar(select(func.count()).select_from(User)) or 0
        initial_admin_exists = db.scalar(
            select(User.id).where(User.is_initial_admin.is_(True))
        )
        canonical_email = UserService.canonicalize_email(user_data.email)
        if canonical_email is None:
            raise BadRequestException("Invalid email address")

        is_protected_bootstrap_email = (
            settings.requires_protected_admin_bootstrap
            and settings.bootstrap_admin_email == canonical_email
        )
        if is_protected_bootstrap_email and (
            not bootstrap_token
            or not settings.bootstrap_admin_token
            or not secrets.compare_digest(
                bootstrap_token, settings.bootstrap_admin_token
            )
        ):
            raise BadRequestException("Invalid bootstrap administrator credentials")

        claims_initial_admin = initial_admin_exists is None and (
            (
                settings.is_self_hosted
                and not settings.requires_protected_admin_bootstrap
                and user_count == 0
            )
            or is_protected_bootstrap_email
        )
        new_user = UserService._new_user(
            db,
            user_data,
            Role.ADMIN if claims_initial_admin else Role.USER,
            claims_initial_admin=claims_initial_admin,
        )
        db.add(new_user)

        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            if UserService.get_user_by_email(db, user_data.email) is not None:
                raise BadRequestException("Email already registered") from exc

            initial_admin_exists = db.scalar(
                select(User.id).where(User.is_initial_admin.is_(True))
            )
            if not claims_initial_admin or initial_admin_exists is None:
                raise BadRequestException("Unable to register user") from exc

            # Another first-user request won the unique bootstrap-admin claim.
            new_user = UserService._new_user(
                db,
                user_data,
                Role.USER,
                claims_initial_admin=False,
            )
            db.add(new_user)
            try:
                db.commit()
            except IntegrityError as retry_exc:
                db.rollback()
                raise BadRequestException("Email already registered") from retry_exc

        db.refresh(new_user)
        return new_user

    @staticmethod
    def _new_user(
        db: Session,
        user_data: UserCreate,
        role: Role,
        *,
        claims_initial_admin: bool,
    ) -> User:
        canonical_email = UserService.canonicalize_email(user_data.email)
        if canonical_email is None:
            raise BadRequestException("Invalid email address")

        models = get_available_models()
        default_model = str(models[0]["id"]) if models else "gemini:gemini-3.6-flash"
        for m in models:
            if m.get("is_default"):
                default_model = str(m["id"])
                break

        return User(
            name=user_data.name,
            email=canonical_email,
            password_hash=get_password_hash(user_data.password),
            role=UserService._get_role(db, role),
            is_initial_admin=True if claims_initial_admin else None,
            credits=None if role == Role.ADMIN else 50.0,
            is_banned=False,
            preferred_model=default_model,
        )

    @staticmethod
    def update_user(db: Session, email: str, update_data: UserUpdate) -> UserResponse:
        """Updates specific fields of a user by email."""
        user = UserService.get_user_by_email(db, email)
        if not user:
            raise NotFoundException("User not found")

        update_dict = update_data.model_dump(exclude_unset=True)
        role = update_dict.pop("role", None)
        if role is not None:
            previous_role = Role(user.role.name)
            user.role = UserService._get_role(db, role)
            if role == Role.ADMIN:
                user.credits = None
            elif previous_role == Role.ADMIN and user.credits is None:
                user.credits = 50.0

        pref_model = update_dict.get("preferred_model")
        if pref_model is not None:
            available_model_ids = {m["id"] for m in get_available_models()}
            if pref_model not in available_model_ids:
                raise BadRequestException(f"Unsupported AI model: {pref_model}")

        for field, value in update_dict.items():
            setattr(user, field, value)

        db.commit()
        db.refresh(user)
        return UserService.to_response(user)

    @staticmethod
    def charge_credits(db: Session, user_id: int, amount: float = 1.0) -> bool:
        """Atomically deducts credits from a user. Returns True if successful, False if insufficient credits."""
        user = db.get(User, user_id)
        if user is None:
            return False
        if user.credits is None:
            # Admin has unlimited credits
            return True
        if user.credits < amount:
            return False

        stmt = (
            update(User)
            .where(
                User.id == user_id,
                (User.credits.is_(None) | (User.credits >= amount)),
            )
            .values(credits=User.credits - amount)
        )
        result = db.execute(stmt)
        if result.rowcount == 0:
            return False
        db.commit()
        return True

    @staticmethod
    def refund_credits(db: Session, user_id: int, amount: float = 1.0) -> None:
        """Atomically refunds credits to a user upon generation failure."""
        stmt = (
            update(User)
            .where(User.id == user_id, User.credits.is_not(None))
            .values(credits=User.credits + amount)
        )
        db.execute(stmt)
        db.commit()
