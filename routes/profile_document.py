"""HTTP routes for user-scoped profile document uploads."""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from backend.app.database import get_db
from schemas.profile_document import (
    ProfileDocumentResponse,
    ProfileDocumentStatusResponse,
    ProfileDocumentUploadResponse,
    ProfileProcessingJobResponse,
)
from schemas.response import BaseResponse
from schemas.user import UserResponse
from services.document_validation import UPLOAD_ERRORS, DocumentValidationError
from services.document_hash import FileHashError
from services.profile_document import (
    ProfileDocumentDeletionError,
    ProfileDocumentRegistrationError,
    ProfileDocumentService,
)
from storage.base import Storage
from storage.dependencies import get_storage
from utils.deps import get_current_user
from utils.exceptions import ConflictException, NotFoundException

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/profile-documents", tags=["Profile Documents"])


@router.post("", response_model=BaseResponse[ProfileDocumentUploadResponse], status_code=status.HTTP_201_CREATED)
def upload_profile_document(
    document: Annotated[UploadFile, File(...)],
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> BaseResponse[ProfileDocumentUploadResponse]:
    """Upload and enqueue a user profile background document."""
    try:
        result = ProfileDocumentService.register(
            db=db,
            storage=storage,
            upload=document,
            user_id=current_user.id,
        )
        return BaseResponse(
            success=True,
            data=ProfileDocumentUploadResponse(
                document=ProfileDocumentResponse.model_validate(result.document),
                duplicate=result.duplicate,
            ),
            message="Profile document uploaded successfully."
            if not result.duplicate
            else "Profile document already uploaded.",
        )
    except DocumentValidationError as exc:
        error = UPLOAD_ERRORS.get(exc.code, {"message": str(exc), "status_code": 400})
        raise HTTPException(
            status_code=error["status_code"],
            detail=error["message"],
        ) from exc
    except FileHashError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The uploaded file could not be read safely.",
        ) from exc
    except ConflictException as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.detail,
        ) from exc
    except NotFoundException as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=exc.detail,
        ) from exc
    except ProfileDocumentRegistrationError as exc:
        logger.exception("Failed to register profile document")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The profile document could not be registered.",
        ) from exc


@router.get("", response_model=BaseResponse[list[ProfileDocumentResponse]])
def list_profile_documents(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> BaseResponse[list[ProfileDocumentResponse]]:
    """List all profile documents owned by the current user."""
    documents = ProfileDocumentService.list_user_documents(db, current_user.id)
    return BaseResponse(
        success=True,
        message="Profile documents retrieved.",
        data=[ProfileDocumentResponse.model_validate(doc) for doc in documents],
    )


@router.get("/{document_id}", response_model=BaseResponse[ProfileDocumentStatusResponse])
def get_profile_document_status(
    document_id: UUID,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> BaseResponse[ProfileDocumentStatusResponse]:
    """Get the detail and processing status for one profile document."""
    doc = ProfileDocumentService.get_user_document(db, current_user.id, document_id)
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile document not found.",
        )
    job = ProfileDocumentService.get_document_job(db, current_user.id, document_id)
    return BaseResponse(
        success=True,
        message="Profile document status retrieved.",
        data=ProfileDocumentStatusResponse(
            document=ProfileDocumentResponse.model_validate(doc),
            processing_job=ProfileProcessingJobResponse.model_validate(job) if job else None,
        ),
    )


@router.post("/{document_id}/retry", response_model=BaseResponse[ProfileDocumentStatusResponse])
def retry_profile_document(
    document_id: UUID,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> BaseResponse[ProfileDocumentStatusResponse]:
    """Retry extraction for a failed profile document."""
    try:
        doc, job = ProfileDocumentService.retry_document(db, current_user.id, document_id)
        return BaseResponse(
            success=True,
            data=ProfileDocumentStatusResponse(
                document=ProfileDocumentResponse.model_validate(doc),
                processing_job=ProfileProcessingJobResponse.model_validate(job),
            ),
            message="Profile document processing retried.",
        )
    except ConflictException as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.detail,
        ) from exc


@router.delete("/{document_id}", response_model=BaseResponse[dict[str, str]])
def delete_profile_document(
    document_id: UUID,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> BaseResponse[dict[str, str]]:
    """Permanently delete a profile document, its chunks, and stored embeddings."""
    try:
        ProfileDocumentService.delete_document(db, storage, current_user.id, document_id)
        return BaseResponse(
            success=True,
            data={"id": str(document_id)},
            message="Profile document deleted successfully.",
        )
    except NotFoundException as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=exc.detail,
        ) from exc
    except ConflictException as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.detail,
        ) from exc
    except ProfileDocumentDeletionError as exc:
        logger.exception("Failed to delete profile document %s", document_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The profile document could not be deleted.",
        ) from exc
