from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from schemas.user import UserResponse


class CreditReason(str, Enum):
    INITIAL_GRANT = "initial_grant"
    PERIODIC_GRANT = "periodic_grant"
    GENERATION_CHARGE = "generation_charge"
    GENERATION_REFUND = "generation_refund"
    ADMIN_GRANT = "admin_grant"
    SUPPORT_COMPENSATION = "support_compensation"
    ADMIN_ADJUSTMENT = "admin_adjustment"
    METERING_RESET = "metering_reset"
    MIGRATION_RECONCILIATION = "migration_reconciliation"


class CreditActorType(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ADMIN = "admin"
    MIGRATION = "migration"


POSITIVE_ONLY_ADMIN_REASONS = frozenset(
    {CreditReason.ADMIN_GRANT, CreditReason.SUPPORT_COMPENSATION}
)

ADMIN_CREDIT_REASONS = POSITIVE_ONLY_ADMIN_REASONS | {CreditReason.ADMIN_ADJUSTMENT}


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


class CreditChangeRequest(BaseModel):
    """One administrator credit change: how much, why, and optional human context.

    ``reason`` is mandatory and machine-readable so history stays aggregatable,
    while ``note`` explains the one case. Which signs a reason permits is a
    property of the request rather than of the account, so it is settled here
    before anything reaches the ledger.
    """

    delta: float = Field(allow_inf_nan=False)
    reason: Literal[
        CreditReason.ADMIN_GRANT,
        CreditReason.SUPPORT_COMPENSATION,
        CreditReason.ADMIN_ADJUSTMENT,
    ]
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

    @model_validator(mode="after")
    def reject_sign_the_reason_forbids(self) -> "CreditChangeRequest":
        if self.reason in POSITIVE_ONLY_ADMIN_REASONS and self.delta < 0:
            raise ValueError(
                "A grant must add credits; use Administrator adjustment to remove them."
            )
        return self


class CreditMutationResponse(BaseModel):
    """An administrator credit change: the new balance and the row proving it."""

    user: UserResponse
    transaction: CreditTransactionResponse
