import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.database import get_db
from schemas.auth import (
    EmailVerificationRequest,
    EmailVerificationResendRequest,
    EmailVerificationResponse,
    PasswordResetConfirm,
    PasswordResetRequest,
    RegistrationResponse,
    Token,
)
from schemas.user import UserCreate, UserResponse
from services.email_delivery import EmailDeliveryError
from services.email_verification import (
    EmailVerificationService,
    InvalidVerificationTokenError,
)
from services.user import UserService
from services.password_reset import InvalidPasswordResetTokenError, PasswordResetService
from services.token_revocation import TokenRevocationService
from utils.deps import get_current_user, oauth2_scheme
from utils.exceptions import ConflictException
from utils.rate_limit import (
    client_ip,
    enforce,
    rate_limit_register,
    rate_limit_verification,
    rate_limit_password_reset,
)
from utils.security import create_access_token, decode_access_token, verify_password

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["Authentication"])

VERIFICATION_SENT_MESSAGE = (
    "Check your inbox for a verification link. Your starting credits are added "
    "once the address is confirmed."
)
VERIFICATION_UNDELIVERABLE_MESSAGE = (
    "Your account was created, but the verification email could not be sent. "
    "Request a new link to finish setting it up."
)
VERIFICATION_DISABLED_MESSAGE = "This deployment does not verify email addresses."
INVALID_VERIFICATION_TOKEN_MESSAGE = (
    "This verification link is invalid or has expired. Request a new one."
)
# Deliberately the same message whether or not the address exists: a distinct
# reply would turn this endpoint into a way to test which addresses are
# registered.
RESEND_ACCEPTED_MESSAGE = (
    "If that address belongs to an unverified account, a new verification link "
    "is on its way."
)
RESET_SENT_MESSAGE = (
    "If that address belongs to an account, a password reset link is on its way."
)


@router.post(
    "/register",
    response_model=RegistrationResponse,
    dependencies=[Depends(rate_limit_register)],
)
def register_user(
    user: UserCreate,
    db: Annotated[Session, Depends(get_db)],
    bootstrap_token: Annotated[str | None, Header(alias="X-Bootstrap-Token")] = None,
):
    """
    Handles user registration. Hashes password and prepares it for the database service.

    Where verification is required the account is created unverified and holds
    no spendable introductory credits; redeeming the emailed link is what grants
    them. A relay failure does not undo the registration -- the account exists
    and the resend endpoint is the way back to a working link.
    """
    created_user = UserService.create_user(db, user, bootstrap_token)

    message = "User registered successfully"
    if settings.email_verification_required:
        message = VERIFICATION_SENT_MESSAGE
        try:
            EmailVerificationService.issue_and_send(db, created_user)
        except EmailDeliveryError:
            message = VERIFICATION_UNDELIVERABLE_MESSAGE
            logger.warning(
                "Verification email could not be delivered",
                extra={
                    "event": "verification_email_undelivered",
                    "user_id": created_user.id,
                },
            )

    return RegistrationResponse(
        message=message,
        user_email=created_user.email,
        role=created_user.role.name,
        email_verification_required=settings.email_verification_required,
        is_email_verified=created_user.email_verified_at is not None,
    )


@router.post("/login", response_model=Token)
def login_user(
    request: Request,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Handles user login and returns a JWT Access Token.
    Note: OAuth2 standard expects 'username'. We use email as the username.
    """
    # Checked before any password verification, so a lockout also caps the
    # number of bcrypt comparisons an attacker can force per window.
    enforce(
        db,
        f"login:ip:{client_ip(request)}",
        window_seconds=settings.rate_limit_login_window_seconds,
        limit=settings.rate_limit_login_max_attempts,
        error_code="login_rate_limited",
    )
    account_key = (
        UserService.canonicalize_email(form_data.username)
        or form_data.username.strip().lower()
    )
    enforce(
        db,
        f"login:account:{account_key}",
        window_seconds=settings.rate_limit_login_window_seconds,
        limit=settings.rate_limit_login_max_attempts,
        lockout_base_seconds=settings.rate_limit_lockout_base_seconds,
        lockout_max_seconds=settings.rate_limit_lockout_max_seconds,
        error_code="login_rate_limited",
    )

    user = UserService.get_user_by_email(db, form_data.username)
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if user.is_banned:
        raise HTTPException(status_code=403, detail="Your account has been banned.")

    # An unverified account signs in deliberately. It has to: the screen that
    # explains why the balance is zero, and the control that resends the link,
    # are both behind the session.
    access_token = create_access_token(data={"sub": user.email})

    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/logout")
def logout_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Handles user logout by adding the token's JTI to the denylist.
    """
    try:
        payload = decode_access_token(token)
        jti = payload.get("jti")
        exp = payload.get("exp")
        
        if jti and exp:
            from datetime import datetime, timezone
            expires_at = datetime.fromtimestamp(exp, tz=timezone.utc)
            subject = payload.get("sub")
            user_id = None
            if isinstance(subject, str):
                user = UserService.get_user_by_email(db, subject)
                if user:
                    user_id = user.id
            TokenRevocationService.revoke_token(db, jti, expires_at, user_id=user_id)
            db.commit()
    except Exception:
        # If token is invalid or expired, we don't care during logout
        pass

    return {"message": "Logged out successfully"}


@router.post(
    "/verify-email",
    response_model=EmailVerificationResponse,
    dependencies=[Depends(rate_limit_verification)],
)
def verify_email(
    payload: EmailVerificationRequest,
    db: Annotated[Session, Depends(get_db)],
):
    """Redeem one verification link.

    Unauthenticated on purpose: the link is clicked from a mail client that may
    not carry the session, and the token itself is the proof.
    """
    try:
        user, granted = EmailVerificationService.redeem(db, payload.token)
    except InvalidVerificationTokenError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=INVALID_VERIFICATION_TOKEN_MESSAGE,
        ) from None

    return EmailVerificationResponse(
        message="Email address verified.",
        is_email_verified=user.email_verified_at is not None,
        credits_granted=granted,
    )


@router.post(
    "/verify-email/resend",
    response_model=EmailVerificationResponse,
    dependencies=[Depends(rate_limit_verification)],
)
def resend_verification_email(
    payload: EmailVerificationResendRequest,
    db: Annotated[Session, Depends(get_db)],
):
    """Issue a fresh verification link, replacing any outstanding one.

    Answers identically for an unknown address, an already verified one, and a
    genuine resend, so it cannot be used to enumerate accounts. The only
    distinguishable answer is the one that says this deployment does not verify
    addresses at all, which is a property of the server rather than of anyone's
    account.
    """
    if not settings.email_verification_required:
        raise ConflictException(VERIFICATION_DISABLED_MESSAGE)

    user = UserService.get_user_by_email(db, payload.email)
    if user is not None and user.email_verified_at is None and not user.is_banned:
        try:
            EmailVerificationService.issue_and_send(db, user)
        except EmailDeliveryError:
            logger.warning(
                "Verification email could not be delivered",
                extra={
                    "event": "verification_email_undelivered",
                    "user_id": user.id,
                },
            )

    return EmailVerificationResponse(
        message=RESEND_ACCEPTED_MESSAGE,
        is_email_verified=False,
    )


@router.get("/me", response_model=UserResponse)
def read_users_me(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
):
    """
    Protected endpoint to test token verification.
    Requires a valid JWT Bearer token to access.
    """
    return current_user


@router.post(
    "/reset-password",
    dependencies=[Depends(rate_limit_password_reset)],
)
def request_password_reset(
    payload: PasswordResetRequest,
    db: Annotated[Session, Depends(get_db)],
):
    """
    Issue a password reset link to the given address, if it exists.
    """
    user = UserService.get_user_by_email(db, payload.email)
    if user is not None and not user.is_banned:
        try:
            PasswordResetService.issue_and_send(db, user)
        except EmailDeliveryError:
            logger.warning(
                "Password reset email could not be delivered",
                extra={
                    "event": "password_reset_email_undelivered",
                    "user_id": user.id,
                },
            )

    return {"message": RESET_SENT_MESSAGE}


@router.post("/reset-password/confirm")
def confirm_password_reset(
    payload: PasswordResetConfirm,
    db: Annotated[Session, Depends(get_db)],
):
    """
    Redeem a password reset link and set a new password.
    """
    try:
        user = PasswordResetService.redeem(db, payload.token)
    except InvalidPasswordResetTokenError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This password reset link is invalid or has expired.",
        ) from None

    UserService.force_change_password(db, user, payload.new_password)
    
    return {"message": "Password has been reset successfully. You can now log in."}
