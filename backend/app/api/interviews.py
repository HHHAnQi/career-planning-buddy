"""Batch 1 InterviewSession HTTP boundary."""

from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, UploadFile

from app.api.dependencies import (
    get_current_user,
    get_interview_audio_service,
    get_interview_coaching_service,
    get_interview_service,
)
from app.core.security import AuthenticatedUser
from app.schemas.interviews import (
    InterviewAnswerRequest,
    InterviewComparison,
    InterviewCreateRequest,
    InterviewListResponse,
    InterviewMemoryCandidateRequest,
    InterviewMemoryCandidateResponse,
    InterviewRetestRequest,
    InterviewRunResponse,
    InterviewSessionResponse,
    InterviewVersionRequest,
    TrainingActionsConfirmResponse,
    TrainingActionsPreviewResponse,
    TrainingActionsRequest,
)
from app.services.interview_audio import InterviewAudioService
from app.services.interview_coaching import InterviewCoachingService
from app.services.interviews import InterviewService

router = APIRouter(prefix="/interviews", tags=["interviews"])


@router.post(
    "/{interview_id}/audio-answers",
    status_code=HTTPStatus.ACCEPTED,
    response_model=InterviewRunResponse,
)
async def submit_interview_audio_answer(
    interview_id: UUID,
    turn_id: Annotated[UUID, Form()],
    version: Annotated[int, Form(ge=1)],
    audio: Annotated[UploadFile, File()],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[InterviewAudioService, Depends(get_interview_audio_service)],
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1, max_length=64)
    ],
    fallback_text: Annotated[str | None, Form(max_length=10_000)] = None,
) -> InterviewRunResponse:
    media_type = (audio.content_type or "application/octet-stream").lower()
    filename = audio.filename or "answer.audio"
    try:
        payload = await audio.read()
    finally:
        await audio.close()
    return await service.submit(
        interview_id=interview_id,
        user_id=current_user.id,
        turn_id=turn_id,
        version=version,
        audio=payload,
        media_type=media_type,
        filename=filename,
        fallback_text=fallback_text,
        idempotency_key=idempotency_key,
    )


@router.post("", status_code=HTTPStatus.ACCEPTED, response_model=InterviewRunResponse)
async def create_interview(
    payload: InterviewCreateRequest,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[InterviewService, Depends(get_interview_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=64)],
) -> InterviewRunResponse:
    return await service.create(
        user_id=current_user.id,
        payload=payload,
        idempotency_key=idempotency_key,
    )


@router.get("", response_model=InterviewListResponse)
async def list_interviews(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[InterviewService, Depends(get_interview_service)],
) -> InterviewListResponse:
    return InterviewListResponse(items=await service.list(current_user.id))


@router.get("/{interview_id}", response_model=InterviewSessionResponse)
async def get_interview(
    interview_id: UUID,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[InterviewService, Depends(get_interview_service)],
) -> InterviewSessionResponse:
    return await service.get(interview_id, current_user.id)


@router.delete("/{interview_id}", status_code=HTTPStatus.NO_CONTENT)
async def delete_interview(
    interview_id: UUID,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[InterviewService, Depends(get_interview_service)],
) -> None:
    await service.delete(interview_id, current_user.id)


@router.post(
    "/{interview_id}/start/retry",
    status_code=HTTPStatus.ACCEPTED,
    response_model=InterviewRunResponse,
)
async def retry_interview_start(
    interview_id: UUID,
    payload: InterviewVersionRequest,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[InterviewService, Depends(get_interview_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=64)],
) -> InterviewRunResponse:
    return await service.retry_start(
        interview_id=interview_id,
        user_id=current_user.id,
        version=payload.version,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/{interview_id}/answers",
    status_code=HTTPStatus.ACCEPTED,
    response_model=InterviewRunResponse,
)
async def submit_interview_answer(
    interview_id: UUID,
    payload: InterviewAnswerRequest,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[InterviewService, Depends(get_interview_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=64)],
) -> InterviewRunResponse:
    return await service.submit_answer(
        interview_id=interview_id,
        user_id=current_user.id,
        payload=payload,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/{interview_id}/turns/{turn_id}/skip",
    status_code=HTTPStatus.ACCEPTED,
    response_model=InterviewRunResponse,
)
async def skip_interview_turn(
    interview_id: UUID,
    turn_id: UUID,
    payload: InterviewVersionRequest,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[InterviewService, Depends(get_interview_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=64)],
) -> InterviewRunResponse:
    return await service.skip_turn(
        interview_id=interview_id,
        user_id=current_user.id,
        turn_id=turn_id,
        version=payload.version,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/{interview_id}/finish",
    status_code=HTTPStatus.ACCEPTED,
    response_model=InterviewRunResponse,
)
async def finish_interview(
    interview_id: UUID,
    payload: InterviewVersionRequest,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[InterviewService, Depends(get_interview_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=64)],
) -> InterviewRunResponse:
    return await service.finish(
        interview_id=interview_id,
        user_id=current_user.id,
        version=payload.version,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/{interview_id}/report/retry",
    status_code=HTTPStatus.ACCEPTED,
    response_model=InterviewRunResponse,
)
async def retry_interview_report(
    interview_id: UUID,
    payload: InterviewVersionRequest,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[InterviewService, Depends(get_interview_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=64)],
) -> InterviewRunResponse:
    return await service.finish(
        interview_id=interview_id,
        user_id=current_user.id,
        version=payload.version,
        idempotency_key=idempotency_key,
        retry=True,
    )


@router.post(
    "/{interview_id}/memory-candidates", response_model=InterviewMemoryCandidateResponse
)
async def create_interview_memory_candidates(
    interview_id: UUID,
    payload: InterviewMemoryCandidateRequest,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[InterviewCoachingService, Depends(get_interview_coaching_service)],
) -> InterviewMemoryCandidateResponse:
    return await service.create_memory_candidates(
        interview_id=interview_id, user_id=current_user.id, payload=payload
    )


@router.post(
    "/{interview_id}/training-actions/preview", response_model=TrainingActionsPreviewResponse
)
async def preview_interview_training_actions(
    interview_id: UUID,
    payload: TrainingActionsRequest,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[InterviewCoachingService, Depends(get_interview_coaching_service)],
) -> TrainingActionsPreviewResponse:
    return await service.preview_training_actions(
        interview_id=interview_id, user_id=current_user.id, payload=payload
    )


@router.post(
    "/{interview_id}/training-actions/confirm", response_model=TrainingActionsConfirmResponse
)
async def confirm_interview_training_actions(
    interview_id: UUID,
    payload: TrainingActionsRequest,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[InterviewCoachingService, Depends(get_interview_coaching_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=64)],
) -> TrainingActionsConfirmResponse:
    return await service.confirm_training_actions(
        interview_id=interview_id,
        user_id=current_user.id,
        payload=payload,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/{interview_id}/retest", status_code=HTTPStatus.ACCEPTED, response_model=InterviewRunResponse
)
async def create_interview_retest(
    interview_id: UUID,
    payload: InterviewRetestRequest,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[InterviewCoachingService, Depends(get_interview_coaching_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=64)],
) -> InterviewRunResponse:
    return await service.create_retest(
        interview_id=interview_id,
        user_id=current_user.id,
        payload=payload,
        idempotency_key=idempotency_key,
    )


@router.get("/{interview_id}/comparison", response_model=InterviewComparison)
async def get_interview_comparison(
    interview_id: UUID,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[InterviewCoachingService, Depends(get_interview_coaching_service)],
) -> InterviewComparison:
    return await service.get_comparison(interview_id=interview_id, user_id=current_user.id)
