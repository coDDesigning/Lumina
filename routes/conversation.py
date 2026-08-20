from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.database import get_db
from schemas.conversation import ConversationDetail, ConversationSummary
from schemas.response import BaseResponse
from services.conversation import ConversationService
from utils.authorization import AuthorizedCourse

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
