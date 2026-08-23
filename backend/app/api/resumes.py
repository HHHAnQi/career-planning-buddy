"""Immutable ResumeVersion and JobTarget HTTP resources."""

import logging
from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Header, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    get_current_user,
    get_embedding_provider,
    get_resume_document_service,
    get_resume_service,
)
from app.core.database import get_db_session
from app.core.security import AuthenticatedUser
from app.providers.embedding import EmbeddingProvider
from app.schemas.resumes import (
    JobTargetCreateRequest,
    JobTargetListResponse,
    JobTargetResponse,
    ResumeDocumentExtractResponse,
    ResumeVersionCreateRequest,
    ResumeVersionListResponse,
    ResumeVersionResponse,
)
from app.services.rag_documents import ingest_untrusted_document
from app.services.resume_documents import MAX_RESUME_FILE_BYTES, ResumeDocumentService
from app.services.resumes import ResumeService

logger = logging.getLogger(__name__)

resume_router = APIRouter(prefix="/resume-versions", tags=["resume-versions"])
job_target_router = APIRouter(prefix="/job-targets", tags=["job-targets"])


@resume_router.post("/extract", response_model=ResumeDocumentExtractResponse)
async def extract_resume_document(
    file: Annotated[UploadFile, File()],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[ResumeDocumentService, Depends(get_resume_document_service)],
) -> ResumeDocumentExtractResponse:
    _ = current_user
    filename = file.filename or ""
    media_type = file.content_type or "application/octet-stream"
    try:
        content = await file.read(MAX_RESUME_FILE_BYTES + 1)
    finally:
        await file.close()
    return service.extract(filename=filename, media_type=media_type, content=content)


@resume_router.post("", status_code=HTTPStatus.CREATED, response_model=ResumeVersionResponse)
async def create_resume_version(
    payload: ResumeVersionCreateRequest,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[ResumeService, Depends(get_resume_service)],
    session: Annotated[AsyncSession, Depends(get_db_session, use_cache=False)],
    embedding_provider: Annotated[EmbeddingProvider, Depends(get_embedding_provider)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=64)],
) -> ResumeVersionResponse:
    response = await service.create_resume(
        user_id=current_user.id,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    await _ingest_best_effort(
        session,
        embedding_provider=embedding_provider,
        user_id=current_user.id,
        doc_kind="resume",
        source_id=response.resume_version_id,
        title=response.label,
        text=response.source_text,
    )
    return response


async def _ingest_best_effort(
    session: AsyncSession,
    *,
    embedding_provider: EmbeddingProvider,
    user_id: UUID,
    doc_kind: str,
    source_id: UUID,
    title: str,
    text: str,
) -> None:
    """Index the document into the retrieval corpus; best-effort only.

    Ingestion must never fail the resume/JD creation that triggered it:
    the document stays searchable lexically even when embedding fails,
    and a later re-ingest is idempotent (delete-then-insert).
    """

    try:
        await ingest_untrusted_document(
            session,
            embedding_provider=embedding_provider,
            user_id=user_id,
            doc_kind=doc_kind,
            source_id=source_id,
            title=title,
            text=text,
        )
    except Exception:  # noqa: BLE001 - best-effort by contract
        logger.warning(
            "rag ingest failed for %s %s; re-ingest stays idempotent",
            doc_kind,
            source_id,
            exc_info=True,
        )


@resume_router.get("", response_model=ResumeVersionListResponse)
async def list_resume_versions(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[ResumeService, Depends(get_resume_service)],
) -> ResumeVersionListResponse:
    return ResumeVersionListResponse(items=await service.list_resumes(current_user.id))


@resume_router.delete("/{resume_id}", status_code=HTTPStatus.NO_CONTENT)
async def delete_resume_version(
    resume_id: UUID,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[ResumeService, Depends(get_resume_service)],
) -> None:
    await service.delete_resume(resume_id, current_user.id)


@job_target_router.post("", status_code=HTTPStatus.CREATED, response_model=JobTargetResponse)
async def create_job_target(
    payload: JobTargetCreateRequest,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[ResumeService, Depends(get_resume_service)],
    session: Annotated[AsyncSession, Depends(get_db_session, use_cache=False)],
    embedding_provider: Annotated[EmbeddingProvider, Depends(get_embedding_provider)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=64)],
) -> JobTargetResponse:
    response = await service.create_job_target(
        user_id=current_user.id,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    await _ingest_best_effort(
        session,
        embedding_provider=embedding_provider,
        user_id=current_user.id,
        doc_kind="job_target",
        source_id=response.job_target_id,
        title=response.title,
        text=response.jd_text,
    )
    return response


@job_target_router.get("", response_model=JobTargetListResponse)
async def list_job_targets(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[ResumeService, Depends(get_resume_service)],
) -> JobTargetListResponse:
    return JobTargetListResponse(items=await service.list_job_targets(current_user.id))


@job_target_router.delete("/{target_id}", status_code=HTTPStatus.NO_CONTENT)
async def delete_job_target(
    target_id: UUID,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[ResumeService, Depends(get_resume_service)],
) -> None:
    await service.delete_job_target(target_id, current_user.id)
