"""HTTP-only Memory and consent endpoints."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response, status

from app.api.dependencies import get_current_user, get_memory_service
from app.core.security import AuthenticatedUser
from app.schemas.memories import (
    MemoryCandidateDecisionResponse,
    MemoryCandidateListResponse,
    MemoryListResponse,
    MemoryPatchRequest,
    MemoryResponse,
)
from app.services.memories import MemoryService

router = APIRouter(tags=["memories"])


@router.get("/memories", response_model=MemoryListResponse)
async def list_memories(
    service: Annotated[MemoryService, Depends(get_memory_service)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    memory_type: Annotated[
        Literal["profile_fact", "stable_preference", "execution_pattern"] | None,
        Query(alias="type"),
    ] = None,
    memory_status: Annotated[Literal["active", "closed"], Query(alias="status")] = "active",
    include_sensitive: bool = False,
    cursor: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> MemoryListResponse:
    return await service.list_memories(
        user_id=current_user.id,
        memory_type=memory_type,
        status=memory_status,
        include_sensitive=include_sensitive,
        cursor=cursor,
        limit=limit,
    )


@router.patch("/memories/{memory_id}", response_model=MemoryResponse)
async def patch_memory(
    memory_id: UUID,
    payload: MemoryPatchRequest,
    service: Annotated[MemoryService, Depends(get_memory_service)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> MemoryResponse:
    return await service.patch_memory(memory_id=memory_id, user_id=current_user.id, payload=payload)


@router.delete("/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: UUID,
    service: Annotated[MemoryService, Depends(get_memory_service)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> Response:
    await service.delete_memory(memory_id=memory_id, user_id=current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/memory-candidates", response_model=MemoryCandidateListResponse)
async def list_memory_candidates(
    service: Annotated[MemoryService, Depends(get_memory_service)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    candidate_status: Annotated[
        Literal["pending", "confirmed", "rejected", "expired"], Query(alias="status")
    ] = "pending",
    cursor: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> MemoryCandidateListResponse:
    return await service.list_candidates(
        user_id=current_user.id,
        status=candidate_status,
        cursor=cursor,
        limit=limit,
    )


@router.post(
    "/memory-candidates/{candidate_id}/confirm",
    response_model=MemoryCandidateDecisionResponse,
)
async def confirm_memory_candidate(
    candidate_id: UUID,
    service: Annotated[MemoryService, Depends(get_memory_service)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=64)],
) -> MemoryCandidateDecisionResponse:
    return await service.confirm_candidate(
        candidate_id=candidate_id,
        user_id=current_user.id,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/memory-candidates/{candidate_id}/reject",
    response_model=MemoryCandidateDecisionResponse,
)
async def reject_memory_candidate(
    candidate_id: UUID,
    service: Annotated[MemoryService, Depends(get_memory_service)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=64)],
) -> MemoryCandidateDecisionResponse:
    return await service.reject_candidate(
        candidate_id=candidate_id,
        user_id=current_user.id,
        idempotency_key=idempotency_key,
    )
