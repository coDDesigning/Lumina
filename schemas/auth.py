from pydantic import BaseModel, EmailStr, Field


class Token(BaseModel):
    access_token: str
    token_type: str


class RegistrationResponse(BaseModel):
    """What registration tells the client to do next.

    ``email_verification_required`` is what a hosted client branches on: the
    account exists and can sign in, but it holds no introductory credits until
    the address is verified, so the screen after registration is a prompt to
    check the inbox rather than a workspace.
    """

    message: str
    user_email: EmailStr
    role: str
    email_verification_required: bool
    is_email_verified: bool


class EmailVerificationRequest(BaseModel):
    """One verification token, as it arrived in the emailed link."""

    token: str = Field(min_length=1, max_length=512)


class EmailVerificationResendRequest(BaseModel):
    """A request for a fresh verification link for one address."""

    email: EmailStr = Field(max_length=255)


class EmailVerificationResponse(BaseModel):
    """The outcome of redeeming a verification token."""

    message: str
    is_email_verified: bool
    credits_granted: float | None = None


class PasswordResetRequest(BaseModel):
    """A request to issue a password reset link."""
    email: EmailStr = Field(max_length=255)


class PasswordResetConfirm(BaseModel):
    """Redeems a password reset link to set a new password."""
    token: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=1, max_length=255)
