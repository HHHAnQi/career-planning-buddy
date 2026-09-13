"""Authenticated Agent Run and durable SSE endpoints."""

from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_agent_run_service, get_current_user
from app.core.exceptions import AppError
from app.core.security import AuthenticatedUser
from app.schemas.agent_runs import (
    AgentRunCancelRequest,
    AgentRunCancelResponse,
    AgentRunResponse,
)
from app.schemas.enums import RunStatus
from app.schemas.errors import ErrorResponse
from app.services.agent_runs import AgentRunService

router = APIRouter(prefix="/agent-runs", tags=["agent-runs"])


@router.get(
    "/{run_id}",
    response_model=AgentRunResponse,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def get_agent_run(
    run_id: UUID,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[AgentRunService, Depends(get_agent_run_service)],
) -> AgentRunResponse:
    return await service.get_response(run_id, current_user.id)


@router.get(
    "/{run_id}/events",
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def stream_agent_run_events(
    run_id: UUID,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[AgentRunService, Depends(get_agent_run_service)],
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    try:
        after_sequence = int(last_event_id) if last_event_id is not None else 0
    except ValueError as exc:
        raise AppError(
            code="VALIDATION_RUN_INVALID",
            message="Last-Event-ID must be a non-negative integer",
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        ) from exc
    if after_sequence < 0:
        raise AppError(
            code="VALIDATION_RUN_INVALID",
            message="Last-Event-ID must be a non-negative integer",
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        )
    await service.get(run_id, current_user.id)
    return StreamingResponse(
        service.stream_events(
            run_id=run_id,
            user_id=current_user.id,
            after_sequence=after_sequence,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/{run_id}/cancel",
    status_code=HTTPStatus.ACCEPTED,
    response_model=AgentRunCancelResponse,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def cancel_agent_run(
    run_id: UUID,
    payload: AgentRunCancelRequest,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[AgentRunService, Depends(get_agent_run_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=64)],
) -> AgentRunCancelResponse:
    run = await service.cancel(
        run_id=run_id,
        user_id=current_user.id,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    if run.status == "cancelled":
        response.status_code = HTTPStatus.OK
    return AgentRunCancelResponse(
        run_id=run.id,
        status=RunStatus(run.status),
        cancel_requested=True,
    )
