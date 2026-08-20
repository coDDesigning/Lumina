from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from schemas.user import UserResponse


class CreditReason(str, Enum):
    INITIAL_GRANT = "initial_grant"
    PERIODIC_GRANT = "periodic_grant"
    GENERATION_CHARGE = "generation_charge"
    GENERATION_REFUND = "generation_refund"
    ADMIN_GRANT = "admin_grant"
    ADMIN_ADJUSTMENT = "admin_adjustment"
    METERING_RESET = "metering_reset"
    MIGRATION_RECONCILIATION = "migration_reconciliation"


class CreditActorType(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ADMIN = "admin"
    MIGRATION = "migration"


class CreditTransactionResponse(BaseModel):
    """One immutable accounting event against a single account's balance."""

    id: int
    delta: float
    balance_after: float
    reason: CreditReason
    actor_type: CreditActorType
    actor_user_id: int | None = None
    actor_label: str | None = None
    source_type: str | None = None
    source_id: int | None = None
    refunds_transaction_id: int | None = None
    grant_period: str | None = None
    note: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CreditGrantRequest(BaseModel):
    """Administrator grant. Positive amounts only."""

    amount: float = Field(gt=0)
    note: str | None = Field(default=None, max_length=500)

    @field_validator("note")
    @classmethod
    def reject_note_nul(cls, value: str | None) -> str | None:
        if value is not None and "\x00" in value:
            raise ValueError("Text fields cannot contain NUL characters")
        return value


class CreditAdjustRequest(BaseModel):
    """Administrator correction. Signed, and never zero."""

    delta: float
    note: str | None = Field(default=None, max_length=500)

    @field_validator("delta")
    @classmethod
    def reject_zero_delta(cls, value: float) -> float:
        if value == 0:
            raise ValueError("A credit adjustment must change the balance")
        return value

    @field_validator("note")
    @classmethod
    def reject_note_nul(cls, value: str | None) -> str | None:
        if value is not None and "\x00" in value:
            raise ValueError("Text fields cannot contain NUL characters")
        return value


class CreditMutationResponse(BaseModel):
    """An administrator credit change: the new balance and the row proving it."""

    user: UserResponse
    transaction: CreditTransactionResponse
