import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from schemas.admin_logs import ClientErrorAccepted, ClientErrorReport
from schemas.response import BaseResponse
from schemas.user import UserResponse
from utils.deps import get_current_user
from utils.rate_limit import check_client_error_rate, get_rate_limit_db

router = APIRouter(prefix="/api/client-errors", tags=["Client errors"])
logger = logging.getLogger(__name__)


@router.post(
    "",
    response_model=BaseResponse[ClientErrorAccepted],
    responses={
        401: {"description": "Authentication required"},
        422: {"description": "The report is not valid"},
        429: {"description": "Client error reporting is rate limited"},
    },
)
def report_client_error(
    payload: ClientErrorReport,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    rate_db: Annotated[Session, Depends(get_rate_limit_db)],
):
    is_new = check_client_error_rate(
        rate_db,
        user_id=current_user.id,
        fingerprint=payload.fingerprint,
    )
    if is_new:
        logger.warning(
            "Browser reported an unhandled interface error",
            extra={
                "event": "client_error_reported",
                "application_version": payload.application_version,
                "client_fingerprint": payload.fingerprint,
                "error_class": payload.error_class,
                "http_path": payload.route_template,
                "related_request_id": payload.api_request_id,
                "user_id": current_user.id,
            },
        )
    return BaseResponse(
        success=True,
        message="Client error report accepted"
        if is_new
        else "Duplicate report ignored",
        data=ClientErrorAccepted(accepted=is_new),
    )
