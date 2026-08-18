from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
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
from services.text_generation import (
    TextGenerationError,
    get_text_generation_provider,
)
from utils.deps import get_current_user

router = APIRouter(
    prefix="/api",
    tags=["Prompt Generator"],
)


@router.post(
    "/prompt-generator",
    response_model=BaseResponse[PromptGenerationResponse],
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
        cause = exc.__cause__ if exc.__cause__ is not None else exc
        error_cat = getattr(
            cause, "error_category", getattr(exc, "error_category", None)
        )
        if error_cat == "rate_limit":
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=str(cause or exc),
            ) from exc
        if error_cat == "timeout":
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=str(cause or exc),
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    return BaseResponse(
        success=True,
        message="Prompt generated successfully",
        data=generated_prompt,
    )
