"""HTTP routes for optional hosted advertising configuration and privacy-safe telemetry."""

import logging
from fastapi import APIRouter, status

from backend.app.config import settings
from backend.app.observability import emit_emf_metrics
from schemas.ads import AdConfigResponse, AdTelemetryRequest, AdTelemetryResponse
from schemas.response import BaseResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ads", tags=["Advertising"])


@router.get("/config", response_model=BaseResponse[AdConfigResponse])
def get_ad_config() -> BaseResponse[AdConfigResponse]:
    """Return public advertising configuration.

    In self-hosted mode or when ads are disabled, strictly returns enabled=False
    with no provider metadata.
    """
    if settings.is_self_hosted or not settings.enable_hosted_ads:
        return BaseResponse(
            success=True,
            message="Advertising configuration retrieved.",
            data=AdConfigResponse(
                enabled=False,
                provider=None,
                publisher_id=None,
            ),
        )

    return BaseResponse(
        success=True,
        message="Advertising configuration retrieved.",
        data=AdConfigResponse(
            enabled=True,
            provider=settings.hosted_ads_provider,
            publisher_id=settings.hosted_ads_publisher_id,
        ),
    )


@router.post(
    "/telemetry/impression",
    response_model=BaseResponse[AdTelemetryResponse],
    status_code=status.HTTP_200_OK,
)
def record_ad_telemetry(payload: AdTelemetryRequest) -> BaseResponse[AdTelemetryResponse]:
    """Record aggregate, privacy-safe impression and render status telemetry.

    Never accepts or records study content, user identifiers, or course context.
    """
    if settings.is_self_hosted or not settings.enable_hosted_ads:
        return BaseResponse(
            success=True,
            message="Advertising is disabled.",
            data=AdTelemetryResponse(recorded=False),
        )

    logger.info(
        "Ad telemetry recorded: placement=%s provider=%s status=%s",
        payload.placement,
        payload.provider,
        payload.status,
        extra={
            "event": "ad_telemetry",
            "placement": payload.placement,
            "provider": payload.provider,
            "ad_status": payload.status,
        },
    )

    emit_emf_metrics(
        {"AdImpressions": 1},
        {"Placement": payload.placement, "Status": payload.status},
    )

    return BaseResponse(
        success=True,
        message="Telemetry recorded.",
        data=AdTelemetryResponse(recorded=True),
    )
