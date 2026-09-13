"""Goal draft clarification and confirmation endpoints."""

from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header

from app.api.dependencies import get_current_user, get_goal_brief_service
from app.core.security import AuthenticatedUser
from app.schemas.errors import ErrorResponse
from app.schemas.goal_briefs import (
    GoalBriefConfirmResponse,
    GoalBriefCreateRequest,
    GoalBriefRefineRequest,
    GoalBriefResponse,
    GoalBriefVersionRequest,
)
from app.services.goal_briefs import GoalBriefService

router = APIRouter(prefix="/goal-briefs", tags=["goal-briefs"])


@router.post(
    "",
    status_code=HTTPStatus.CREATED,
    response_model=GoalBriefResponse,
    responses={
        401: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def create_goal_brief(
    payload: GoalBriefCreateRequest,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[GoalBriefService, Depends(get_goal_brief_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=64)],
) -> GoalBriefResponse:
    return service.to_response(
        await service.create(
            user_id=current_user.id, payload=payload, idempotency_key=idempotency_key
        )
    )


@router.get(
    "/{brief_id}",
    response_model=GoalBriefResponse,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def get_goal_brief(
    brief_id: UUID,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[GoalBriefService, Depends(get_goal_brief_service)],
) -> GoalBriefResponse:
    return service.to_response(await service.get(brief_id, current_user.id))


@router.post(
    "/{brief_id}/refine",
    response_model=GoalBriefResponse,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def refine_goal_brief(
    brief_id: UUID,
    payload: GoalBriefRefineRequest,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[GoalBriefService, Depends(get_goal_brief_service)],
) -> GoalBriefResponse:
    return service.to_response(
        await service.refine(brief_id=brief_id, user_id=current_user.id, payload=payload)
    )


@router.post(
    "/{brief_id}/confirm",
    status_code=HTTPStatus.ACCEPTED,
    response_model=GoalBriefConfirmResponse,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def confirm_goal_brief(
    brief_id: UUID,
    payload: GoalBriefVersionRequest,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[GoalBriefService, Depends(get_goal_brief_service)],
) -> GoalBriefConfirmResponse:
    return await service.confirm(
        brief_id=brief_id, user_id=current_user.id, version=payload.version
    )


@router.post(
    "/{brief_id}/cancel",
    response_model=GoalBriefResponse,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def cancel_goal_brief(
    brief_id: UUID,
    payload: GoalBriefVersionRequest,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[GoalBriefService, Depends(get_goal_brief_service)],
) -> GoalBriefResponse:
    return service.to_response(
        await service.cancel(brief_id=brief_id, user_id=current_user.id, version=payload.version)
    )
