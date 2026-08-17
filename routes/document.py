"""HTTP routes for course-scoped document uploads."""

import logging
import math
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import (
    http_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.app.database import get_db
from schemas.document import (
    DocumentResponse,
    DocumentStatusResponse,
    DocumentUploadResponse,
    ProcessingJobResponse,
)
from schemas.response import BaseResponse
from schemas.user import UserResponse
from services.document import (
    CourseDocumentLimitError,
    DocumentActiveError,
    DocumentDeletionError,
    DocumentDeletionInProgressError,
    DocumentRegistrationError,
    DocumentService,
)
from services.document_hash import FileHashError
from services.document_validation import UPLOAD_ERRORS, DocumentValidationError
from storage.base import Storage
from storage.dependencies import get_storage
from utils.authorization import AuthorizedCourse, OwnedCourse
from utils.deps import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/courses", tags=["Documents"])


class UploadErrorData(BaseModel):
    code: str


UploadErrorResponse = BaseResponse[UploadErrorData]


def _status_response(document, job) -> DocumentStatusResponse:
    return DocumentStatusResponse(
        document=DocumentResponse.model_validate(document),
        processing_job=ProcessingJobResponse.model_validate(job),
    )


def _error_response(error_key: str) -> JSONResponse:
    error = UPLOAD_ERRORS[error_key]
    response = UploadErrorResponse(
        success=False,
        message=error["message"],
        data=UploadErrorData(code=error["code"]),
    )
    return JSONResponse(status_code=error["status_code"], content=response.model_dump())


def _is_document_upload(request: Request) -> bool:
    path = request.url.path
    return (
        request.method == "POST"
        and path.startswith("/api/courses/")
        and path.endswith("/documents")
    )


async def upload_request_validation_error(
    request: Request,
    exception: RequestValidationError,
) -> Response:
    missing_document = any(
        error.get("type") == "missing"
        and tuple(error.get("loc", ())) == ("body", "document")
        for error in exception.errors()
    )
    if _is_document_upload(request) and missing_document:
        return _error_response("document_required")
    errors = exception.errors()
    for error in errors:
        invalid_input = error.get("input")
        if isinstance(invalid_input, float) and not math.isfinite(invalid_input):
            error["input"] = str(invalid_input)
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})


async def upload_http_error(
    request: Request,
    exception: StarletteHTTPException,
) -> Response:
    if _is_document_upload(request) and exception.status_code == 400:
        return _error_response("invalid_multipart")
    return await http_exception_handler(request, exception)


@router.post(
    "/{course_id}/documents",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {"model": DocumentUploadResponse, "description": "Existing document"},
        400: {"model": UploadErrorResponse, "description": "Invalid multipart body"},
        401: {"description": "Authentication required"},
        403: {"description": "Account is not allowed to upload"},
        404: {"description": "Course not found"},
        408: {"description": "Upload admission or body timeout"},
        409: {"model": UploadErrorResponse, "description": "Course document limit"},
        413: {"model": UploadErrorResponse, "description": "Document too large"},
        415: {"model": UploadErrorResponse, "description": "Unsupported file type"},
        422: {"description": "Invalid document or request parameters"},
        500: {"model": UploadErrorResponse, "description": "Upload failure"},
    },
)
def upload_document(
    response: Response,
    document: Annotated[UploadFile, File()],
    course: OwnedCourse,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> DocumentUploadResponse | JSONResponse:
    """Register a new document or return its course-scoped duplicate."""
    try:
        result = DocumentService.register(
            db=db,
            storage=storage,
            upload=document,
            course_id=course.id,
            user_id=current_user.id,
        )
    except DocumentValidationError as exc:
        return _error_response(exc.error_key)
    except FileHashError:
        logger.exception("Failed to hash uploaded document")
        return _error_response("upload_failed")
    except CourseDocumentLimitError:
        return _error_response("course_document_limit")
    except DocumentDeletionInProgressError:
        return _error_response("document_deletion_in_progress")
    except DocumentRegistrationError:
        logger.exception("Failed to register uploaded document")
        return _error_response("upload_failed")

    response.status_code = (
        status.HTTP_200_OK if result.duplicate else status.HTTP_201_CREATED
    )
    return DocumentUploadResponse(
        document=DocumentResponse.model_validate(result.document),
        duplicate=result.duplicate,
    )


@router.get(
    "/{course_id}/documents",
    response_model=BaseResponse[list[DocumentResponse]],
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Account is not allowed to access documents"},
        404: {"description": "Course not found"},
    },
)
def get_documents(
    course: AuthorizedCourse,
    db: Annotated[Session, Depends(get_db)],
) -> BaseResponse[list[DocumentResponse]]:
    """List the documents of a course the caller is allowed to read."""
    documents = DocumentService.list_course_documents(db, course.id)

    return BaseResponse(
        success=True,
        message="Documents retrieved successfully",
        data=[DocumentResponse.model_validate(doc) for doc in documents],
    )


@router.get(
    "/{course_id}/documents/{document_id}",
    response_model=DocumentStatusResponse,
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Account is not allowed to access documents"},
        404: {"description": "Course or document not found"},
    },
)
def get_document_status(
    document_id: UUID,
    course: AuthorizedCourse,
    db: Annotated[Session, Depends(get_db)],
) -> DocumentStatusResponse:
    document, job = DocumentService.get_document_job(db, document_id, course.id)
    return _status_response(document, job)


@router.post(
    "/{course_id}/documents/{document_id}/retry",
    response_model=DocumentStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Account is not allowed to retry documents"},
        404: {"description": "Course or document not found"},
        409: {"description": "Only failed document jobs can be retried"},
    },
)
def retry_document(
    document_id: UUID,
    course: OwnedCourse,
    db: Annotated[Session, Depends(get_db)],
) -> DocumentStatusResponse:
    document, job = DocumentService.retry_document(db, document_id, course.id)
    return _status_response(document, job)


@router.delete(
    "/{course_id}/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Account is not allowed to delete documents"},
        404: {"description": "Course or document not found"},
        409: {"description": "Active documents cannot be deleted"},
        500: {"description": "Document deletion failed"},
    },
)
def delete_document(
    document_id: UUID,
    course: OwnedCourse,
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> Response:
    try:
        DocumentService.delete_document(db, storage, document_id, course.id)
    except DocumentActiveError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The document cannot be deleted while it is being processed.",
        ) from exc
    except DocumentDeletionError as exc:
        logger.exception("Document deletion failed for %s", document_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The document could not be deleted; retry the operation.",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
