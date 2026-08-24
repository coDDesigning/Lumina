from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.app.database import get_db
from schemas.activity import ActivityItem
from schemas.response import BaseResponse
from schemas.user import UserResponse
from services.activity import ActivityService
from utils.deps import get_current_user

router = APIRouter(prefix="/api", tags=["Activity"])

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


@router.get(
    "/activity",
    response_model=BaseResponse[list[ActivityItem]],
    responses={
        401: {"description": "Authentication required"},
    },
)
def list_recent_activity(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
):
    items = ActivityService.list_recent(db, user_id=current_user.id, limit=limit)
    return BaseResponse(
        success=True,
        message="Recent activity retrieved successfully",
        data=items,
    )
