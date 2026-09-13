"""Immutable resume-version and job-target use cases."""

import json
import re
from hashlib import sha256
from http import HTTPStatus
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import session_transaction
from app.core.exceptions import AppError
from app.models.resume import JobTarget, ResumeVersion
from app.repositories.resumes import ResumeRepository
from app.schemas.resumes import (
    JobTargetCreateRequest,
    JobTargetResponse,
    ResumeVersionCreateRequest,
    ResumeVersionResponse,
)


def stable_text_items(text: str, *, prefix: str) -> list[dict[str, object]]:
    """Extract stable items with exact source spans for conflict-safe rewriting."""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    chunks: list[tuple[str, str, int, int]] = []
    seen: set[str] = set()
    for match in re.finditer(r"[^\n。]+(?:。|$)", normalized):
        raw = match.group(0)
        leading = len(raw) - len(raw.lstrip(" \t-*•"))
        value = raw.strip(" \t-*•")[:1000]
        if len(value) < 8:
            continue
        start = match.start() + leading
        identity = value if value not in seen else f"{value}:{start}"
        seen.add(value)
        chunks.append((identity, value, start, start + len(value)))
        if len(chunks) >= 80:
            break
    key = "claim_id" if prefix == "claim" else "requirement_id"
    return [
        {
            key: f"{prefix}_{sha256(identity.casefold().encode()).hexdigest()[:16]}",
            "text": value,
            "source_start": start,
            "source_end": end,
            "source_hash": sha256(value.encode()).hexdigest(),
        }
        for identity, value, start, end in chunks
    ]


class ResumeService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = ResumeRepository(session)

    async def create_resume(
        self, *, user_id: UUID, payload: ResumeVersionCreateRequest, idempotency_key: str
    ) -> ResumeVersionResponse:
        request_hash = self._hash(payload.model_dump(mode="json"))
        async with session_transaction(self._session):
            existing = await self._repo.resume_by_key(user_id, idempotency_key)
            if existing is not None:
                self._validate_key(existing.request_hash, request_hash)
                return self._resume_response(existing)
            if (
                payload.parent_version_id is not None
                and await self._repo.get_resume(payload.parent_version_id, user_id) is None
            ):
                raise self._not_found("parent resume version")
            text_value = payload.source_text.strip()
            structured: dict[str, object] = {
                "claims": stable_text_items(text_value, prefix="claim")
            }
            if payload.source_type == "uploaded_file":
                assert payload.source_filename is not None
                safe_filename = payload.source_filename.replace("\\", "/").rsplit("/", 1)[-1]
                structured["source_file"] = {
                    "filename": safe_filename,
                    "media_type": payload.source_media_type,
                }
            row = await self._repo.create_resume(
                ResumeVersion(
                    user_id=user_id,
                    label=payload.label.strip(),
                    source_type=payload.source_type,
                    source_text=text_value,
                    structured_json=structured,
                    content_hash=sha256(text_value.encode()).hexdigest(),
                    parent_version_id=payload.parent_version_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                )
            )
            return self._resume_response(row)

    async def list_resumes(self, user_id: UUID) -> list[ResumeVersionResponse]:
        async with session_transaction(self._session):
            return [self._resume_response(row) for row in await self._repo.list_resumes(user_id)]

    async def delete_resume(self, resume_id: UUID, user_id: UUID) -> None:
        async with session_transaction(self._session):
            row = await self._repo.get_resume(resume_id, user_id)
            if row is None:
                raise self._not_found("resume version")
            await self._repo.soft_delete_resume(row)

    async def create_job_target(
        self, *, user_id: UUID, payload: JobTargetCreateRequest, idempotency_key: str
    ) -> JobTargetResponse:
        request_hash = self._hash(payload.model_dump(mode="json"))
        async with session_transaction(self._session):
            existing = await self._repo.job_target_by_key(user_id, idempotency_key)
            if existing is not None:
                self._validate_key(existing.request_hash, request_hash)
                return self._target_response(existing)
            text_value = payload.jd_text.strip()
            row = await self._repo.create_job_target(
                JobTarget(
                    user_id=user_id,
                    title=payload.title.strip(),
                    company=payload.company.strip() if payload.company else None,
                    jd_text=text_value,
                    requirements_json={
                        "requirements": stable_text_items(text_value, prefix="req")
                    },
                    content_hash=sha256(text_value.encode()).hexdigest(),
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                )
            )
            return self._target_response(row)

    async def list_job_targets(self, user_id: UUID) -> list[JobTargetResponse]:
        async with session_transaction(self._session):
            return [
                self._target_response(row) for row in await self._repo.list_job_targets(user_id)
            ]

    async def delete_job_target(self, target_id: UUID, user_id: UUID) -> None:
        async with session_transaction(self._session):
            row = await self._repo.get_job_target(target_id, user_id)
            if row is None:
                raise self._not_found("job target")
            await self._repo.soft_delete_job_target(row)

    @staticmethod
    def _hash(value: object) -> str:
        return sha256(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @staticmethod
    def _validate_key(actual: str, expected: str) -> None:
        if actual != expected:
            raise AppError(
                code="STATE_IDEMPOTENCY_KEY_REUSED",
                message="Idempotency-Key was already used with another request",
                status_code=HTTPStatus.CONFLICT,
            )

    @staticmethod
    def _not_found(name: str) -> AppError:
        return AppError(
            code="NOT_FOUND_INTERVIEW_INPUT",
            message=f"{name} was not found",
            status_code=HTTPStatus.NOT_FOUND,
        )

    @staticmethod
    def _resume_response(row: ResumeVersion) -> ResumeVersionResponse:
        return ResumeVersionResponse(
            resume_version_id=row.id,
            label=row.label,
            source_type=row.source_type,
            source_text=row.source_text,
            structured=row.structured_json,
            content_hash=row.content_hash,
            parent_version_id=row.parent_version_id,
            created_at=row.created_at,
        )

    @staticmethod
    def _target_response(row: JobTarget) -> JobTargetResponse:
        return JobTargetResponse(
            job_target_id=row.id,
            title=row.title,
            company=row.company,
            jd_text=row.jd_text,
            requirements=row.requirements_json,
            content_hash=row.content_hash,
            created_at=row.created_at,
        )
