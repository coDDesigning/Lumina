from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

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
):
    try:
        provider = get_text_generation_provider()

        generated_prompt = PromptGeneratorService.generate(
            request.description,
            provider,
        )

    except (TextGenerationError, PromptGenerationError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    return BaseResponse(
        success=True,
        message="Prompt generated successfully",
        data=generated_prompt,
    )
