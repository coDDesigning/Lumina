from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.database import get_db
from schemas.prompt_generator import (
    PromptGenerationRequest,
    PromptGenerationResponse,
)
from schemas.response import BaseResponse
from schemas.user import UserResponse
from services.prompt_generator import (
    PromptGenerationError,
    PromptGeneratorService,
)
from services.text_generation import TextGenerationError, get_text_generation_provider
from utils.ai_errors import ai_generation_http_exception
from utils.deps import get_current_user

router = APIRouter(
    prefix="/api",
    tags=["Prompt Generator"],
)


@router.post(
    "/prompt-generator",
    response_model=BaseResponse[PromptGenerationResponse],
    responses={
        429: {"description": "AI provider rate limited"},
        503: {"description": "AI provider unreachable"},
        504: {"description": "AI provider timed out"},
    },
)
def generate_prompt(
    request: PromptGenerationRequest,
    current_user: Annotated[
        UserResponse,
        Depends(get_current_user),
    ],
    db: Annotated[Session, Depends(get_db)],
):
    try:
        provider = get_text_generation_provider()

        generated_prompt = PromptGeneratorService.generate(
            request.description,
            provider,
            db=db,
            user_id=current_user.id,
        )

    except (TextGenerationError, PromptGenerationError) as exc:
        raise ai_generation_http_exception(exc, feature="prompt_generator") from exc

    return BaseResponse(
        success=True,
        message="Prompt generated successfully",
        data=generated_prompt,
    )
