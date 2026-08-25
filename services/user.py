import secrets

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from backend.app.config import settings
from backend.app.models import Role as RoleModel
from backend.app.models import User
from schemas.prompt_context import EducationLevel
from schemas.user import Role, UserCreate, UserResponse, UserUpdate
from services.credits import CreditService
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
            credits=CreditService.reported_balance(user),
            preferred_model=user.preferred_model,
            education_level=user.education_level,
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
    def list_users(db: Session) -> list[UserResponse]:
        """Lists all registered users."""
        users = db.scalars(
            select(User).options(selectinload(User.role)).order_by(User.id)
        ).all()
        return [UserService.to_response(u) for u in users]

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

        user = User(
            name=user_data.name,
            email=canonical_email,
            password_hash=get_password_hash(user_data.password),
            role=UserService._get_role(db, role),
            is_initial_admin=True if claims_initial_admin else None,
            credits=None if role == Role.ADMIN else settings.credit_initial_grant,
            is_banned=False,
            preferred_model=default_model,
        )
        initial_grant = CreditService.build_initial_grant(user)
        if initial_grant is not None:
            user.credit_transactions.append(initial_grant)
        return user

    @staticmethod
    def update_user(db: Session, email: str, update_data: UserUpdate) -> UserResponse:
        """Updates specific fields of a user by email."""
        user = UserService.get_user_by_email(db, email)
        if not user:
            raise NotFoundException("User not found")

        update_dict = update_data.model_dump(exclude_unset=True)
        role = update_dict.pop("role", None)
        if role is not None:
            user.role = UserService._get_role(db, role)
            CreditService.apply_role_metering(db, user, is_admin=role == Role.ADMIN)

        education_level = update_dict.get("education_level")
        if education_level is not None:
            update_dict["education_level"] = EducationLevel(education_level).value

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
