from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.database import get_db
from schemas.profile_knowledge import (
    ProfileKnowledgeCreate,
    ProfileKnowledgeImport,
    ProfileKnowledgeResponse,
    ProfileKnowledgeUpdate,
)
from schemas.response import BaseResponse
from schemas.user import UserResponse
from services.profile_knowledge import ProfileKnowledgeService
from utils.deps import get_current_user

router = APIRouter(prefix="/api/profile-knowledge", tags=["Profile Knowledge"])


@router.post(
    "/",
    response_model=BaseResponse[ProfileKnowledgeResponse],
    status_code=status.HTTP_201_CREATED,
    responses={
        401: {"description": "Authentication required"},
    },
)
def create_profile_knowledge(
    payload: ProfileKnowledgeCreate,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Creates a new student profile knowledge entry owned by the authenticated user."""
    entry = ProfileKnowledgeService.create(db, current_user.id, payload)
    return BaseResponse(
        success=True,
        message="Profile knowledge created successfully",
        data=entry,
    )


@router.post(
    "/import",
    response_model=BaseResponse[list[ProfileKnowledgeResponse]],
    status_code=status.HTTP_201_CREATED,
    responses={
        401: {"description": "Authentication required"},
    },
)
def import_profile_knowledge(
    payload: ProfileKnowledgeImport,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Bulk imports multiple profile knowledge entries for the authenticated user."""
    entries = ProfileKnowledgeService.bulk_import(db, current_user.id, payload)
    return BaseResponse(
        success=True,
        message=f"Successfully imported {len(entries)} profile knowledge entries",
        data=entries,
    )


@router.get(
    "/",
    response_model=BaseResponse[list[ProfileKnowledgeResponse]],
    responses={
        401: {"description": "Authentication required"},
    },
)
def list_profile_knowledge(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Lists all profile knowledge entries owned by the authenticated user."""
    entries = ProfileKnowledgeService.list_by_user(db, current_user.id)
    return BaseResponse(
        success=True,
        message="Profile knowledge retrieved successfully",
        data=entries,
    )


@router.get(
    "/{item_id}",
    response_model=BaseResponse[ProfileKnowledgeResponse],
    responses={
        401: {"description": "Authentication required"},
        404: {"description": "Profile knowledge item not found"},
    },
)
def get_profile_knowledge(
    item_id: int,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Gets a specific profile knowledge entry owned by the authenticated user."""
    entry = ProfileKnowledgeService.get_by_id(db, current_user.id, item_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile knowledge item not found",
        )
    return BaseResponse(
        success=True,
        message="Profile knowledge retrieved successfully",
        data=entry,
    )


@router.put(
    "/{item_id}",
    response_model=BaseResponse[ProfileKnowledgeResponse],
    responses={
        401: {"description": "Authentication required"},
        404: {"description": "Profile knowledge item not found"},
    },
)
def update_profile_knowledge(
    item_id: int,
    payload: ProfileKnowledgeUpdate,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Updates an existing profile knowledge entry owned by the authenticated user."""
    entry = ProfileKnowledgeService.update(db, current_user.id, item_id, payload)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile knowledge item not found",
        )
    return BaseResponse(
        success=True,
        message="Profile knowledge updated successfully",
        data=entry,
    )


@router.delete(
    "/{item_id}",
    response_model=BaseResponse[dict],
    responses={
        401: {"description": "Authentication required"},
        404: {"description": "Profile knowledge item not found"},
    },
)
def delete_profile_knowledge(
    item_id: int,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Deletes an existing profile knowledge entry owned by the authenticated user."""
    deleted = ProfileKnowledgeService.delete(db, current_user.id, item_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile knowledge item not found",
        )
    return BaseResponse(
        success=True,
        message="Profile knowledge deleted successfully",
        data={"id": item_id},
    )


@router.delete(
    "/",
    response_model=BaseResponse[dict],
    responses={
        401: {"description": "Authentication required"},
    },
)
def delete_all_profile_knowledge(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Deletes all profile knowledge entries owned by the authenticated user."""
    count = ProfileKnowledgeService.delete_all(db, current_user.id)
    return BaseResponse(
        success=True,
        message=f"Successfully deleted {count} profile knowledge entries",
        data={"deleted_count": count},
    )

