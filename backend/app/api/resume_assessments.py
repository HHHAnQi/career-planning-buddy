"""Resume Claim Validation HTTP resources."""

from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header

from app.api.dependencies import get_current_user, get_resume_assessment_service
from app.core.security import AuthenticatedUser
from app.schemas.resumes import (
    ResumeAssessmentCreateRequest,
    ResumeAssessmentResponse,
    ResumeOptimizationRunResponse,
    ResumeRewriteApplyResponse,
    ResumeRewriteBatchApplyRequest,
    ResumeRewriteBatchApplyResponse,
    ResumeRewriteDecisionRequest,
    ResumeRewriteDecisionResponse,
)
from app.services.resume_assessments import ResumeAssessmentService

router = APIRouter(prefix="/resume-assessments", tags=["resume-assessments"])


@router.post(
    "/optimize",
    status_code=HTTPStatus.ACCEPTED,
    response_model=ResumeOptimizationRunResponse,
)
async def optimize_resume_with_agent(
    payload: ResumeAssessmentCreateRequest,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[ResumeAssessmentService, Depends(get_resume_assessment_service)],
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1, max_length=64)
    ],
) -> ResumeOptimizationRunResponse:
    return await service.optimize(
        user_id=current_user.id,
        payload=payload,
        idempotency_key=idempotency_key,
    )


@router.post(
    "",
    status_code=HTTPStatus.CREATED,
    response_model=ResumeAssessmentResponse,
    deprecated=True,
)
async def create_resume_assessment(
    payload: ResumeAssessmentCreateRequest,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[ResumeAssessmentService, Depends(get_resume_assessment_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=64)],
) -> ResumeAssessmentResponse:
    return await service.create(
        user_id=current_user.id, payload=payload, idempotency_key=idempotency_key
    )


@router.get("/{assessment_id}", response_model=ResumeAssessmentResponse)
async def get_resume_assessment(
    assessment_id: UUID,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[ResumeAssessmentService, Depends(get_resume_assessment_service)],
) -> ResumeAssessmentResponse:
    return await service.get(assessment_id, current_user.id)


@router.get("", response_model=list[ResumeAssessmentResponse])
async def list_resume_assessments(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[ResumeAssessmentService, Depends(get_resume_assessment_service)],
) -> list[ResumeAssessmentResponse]:
    return await service.list(current_user.id)


@router.put(
    "/{assessment_id}/claims/{claim_id}/decision",
    response_model=ResumeRewriteDecisionResponse,
)
async def decide_resume_rewrite(
    assessment_id: UUID,
    claim_id: str,
    payload: ResumeRewriteDecisionRequest,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[ResumeAssessmentService, Depends(get_resume_assessment_service)],
) -> ResumeRewriteDecisionResponse:
    return await service.decide_rewrite(
        assessment_id=assessment_id,
        claim_id=claim_id,
        user_id=current_user.id,
        payload=payload,
    )


@router.post(
    "/{assessment_id}/claims/{claim_id}/apply",
    response_model=ResumeRewriteApplyResponse,
    deprecated=True,
)
async def apply_resume_rewrite(
    assessment_id: UUID,
    claim_id: str,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[ResumeAssessmentService, Depends(get_resume_assessment_service)],
) -> ResumeRewriteApplyResponse:
    return await service.apply_rewrite(
        assessment_id=assessment_id, claim_id=claim_id, user_id=current_user.id
    )


@router.post(
    "/{assessment_id}/rewrites/apply",
    response_model=ResumeRewriteBatchApplyResponse,
    deprecated=True,
)
async def apply_resume_rewrites_batch(
    assessment_id: UUID,
    payload: ResumeRewriteBatchApplyRequest,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[ResumeAssessmentService, Depends(get_resume_assessment_service)],
) -> ResumeRewriteBatchApplyResponse:
    return await service.apply_rewrites_batch(
        assessment_id=assessment_id,
        user_id=current_user.id,
        payload=payload,
    )


@router.post(
    "/{assessment_id}/rewrites/apply-batch",
    response_model=ResumeRewriteBatchApplyResponse,
)
async def apply_resume_rewrites_batch_r2(
    assessment_id: UUID,
    payload: ResumeRewriteBatchApplyRequest,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[ResumeAssessmentService, Depends(get_resume_assessment_service)],
) -> ResumeRewriteBatchApplyResponse:
    return await service.apply_rewrites_batch(
        assessment_id=assessment_id,
        user_id=current_user.id,
        payload=payload,
    )
