"""Authenticated profile HTTP endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header

from app.api.dependencies import get_current_user, get_profile_service
from app.core.security import AuthenticatedUser
from app.schemas.errors import ErrorResponse
from app.schemas.profile import (
    ProfilePatchRequest,
    ProfilePutRequest,
    ProfileResponse,
)
from app.services.profiles import ProfileService

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get(
    "",
    response_model=ProfileResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Invalid token"},
        404: {"model": ErrorResponse, "description": "Profile not found"},
    },
)
async def get_profile(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[ProfileService, Depends(get_profile_service)],
) -> ProfileResponse:
    profile = await service.get(current_user.id)
    return ProfileResponse.model_validate(profile, from_attributes=True)


@router.put(
    "",
    response_model=ProfileResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Invalid token"},
        409: {"model": ErrorResponse, "description": "Version conflict"},
        422: {"model": ErrorResponse, "description": "Invalid profile"},
    },
)
async def put_profile(
    payload: ProfilePutRequest,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[ProfileService, Depends(get_profile_service)],
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=64),
    ],
) -> ProfileResponse:
    profile = await service.put(
        user_id=current_user.id,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    return ProfileResponse.model_validate(profile, from_attributes=True)


@router.patch(
    "",
    response_model=ProfileResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Invalid token"},
        404: {"model": ErrorResponse, "description": "Profile not found"},
        409: {"model": ErrorResponse, "description": "Version conflict"},
        422: {"model": ErrorResponse, "description": "Invalid profile"},
    },
)
async def patch_profile(
    payload: ProfilePatchRequest,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[ProfileService, Depends(get_profile_service)],
) -> ProfileResponse:
    profile = await service.patch(user_id=current_user.id, payload=payload)
    return ProfileResponse.model_validate(profile, from_attributes=True)
