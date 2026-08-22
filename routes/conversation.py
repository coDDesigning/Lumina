from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.database import get_db
from schemas.conversation import ConversationDetail, ConversationSummary
from schemas.response import BaseResponse
from schemas.user import UserResponse
from services.conversation import ConversationService
from utils.authorization import AuthorizedCourse, OwnedCourse
from utils.deps import get_current_user

router = APIRouter(prefix="/api/courses", tags=["Conversations"])


@router.get(
    "/{course_id}/conversations",
    response_model=BaseResponse[list[ConversationSummary]],
    responses={
        401: {"description": "Authentication required"},
        404: {"description": "Course not found"},
    },
)
def list_conversations(
    course: AuthorizedCourse,
    db: Annotated[Session, Depends(get_db)],
) -> BaseResponse[list[ConversationSummary]]:
    conversations = ConversationService.list_course_conversations(db, course.id)
    return BaseResponse(
        success=True,
        message="Conversations retrieved successfully",
        data=list(conversations),
    )


@router.get(
    "/{course_id}/conversations/{conversation_id}",
    response_model=BaseResponse[ConversationDetail],
    responses={
        401: {"description": "Authentication required"},
        404: {"description": "Course or conversation not found"},
    },
)
def get_conversation(
    conversation_id: int,
    course: AuthorizedCourse,
    db: Annotated[Session, Depends(get_db)],
) -> BaseResponse[ConversationDetail]:
    conversation = ConversationService.get_course_conversation(
        db, course.id, conversation_id
    )
    return BaseResponse(
        success=True,
        message="Conversation retrieved successfully",
        data=ConversationService.detail(conversation),
    )


@router.delete(
    "/{course_id}/conversations/{conversation_id}",
    response_model=BaseResponse[dict],
    responses={
        401: {"description": "Authentication required"},
        404: {"description": "Course or conversation not found"},
    },
)
def delete_conversation(
    conversation_id: int,
    course: OwnedCourse,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> BaseResponse[dict]:
    ConversationService.delete_course_conversation(
        db,
        course_id=course.id,
        conversation_id=conversation_id,
        user_id=current_user.id,
    )
    return BaseResponse(
        success=True,
        message="Conversation deleted successfully",
        data={"id": conversation_id},
    )
