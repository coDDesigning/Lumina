from fastapi import APIRouter

from backend.app.config import settings
from backend.app.legal import (
    POLICY_EFFECTIVE_DATE,
    PRIVACY_POLICY_VERSION,
    TERMS_POLICY_VERSION,
)
from schemas.legal import LegalConfigResponse
from schemas.response import BaseResponse

router = APIRouter(prefix="/api/legal", tags=["Legal"])


@router.get("/config", response_model=BaseResponse[LegalConfigResponse])
def get_legal_config() -> BaseResponse[LegalConfigResponse]:
    if not settings.legal_policies_enabled:
        return BaseResponse(
            success=True,
            message="Legal policy configuration retrieved.",
            data=LegalConfigResponse(enabled=False),
        )

    return BaseResponse(
        success=True,
        message="Legal policy configuration retrieved.",
        data=LegalConfigResponse(
            enabled=True,
            terms_version=TERMS_POLICY_VERSION,
            privacy_version=PRIVACY_POLICY_VERSION,
            effective_date=POLICY_EFFECTIVE_DATE,
        ),
    )
