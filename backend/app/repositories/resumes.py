"""User-scoped persistence for immutable resume versions and job targets."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.resume import JobTarget, ResumeAssessment, ResumeRewriteDecision, ResumeVersion


class ResumeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_resume(
        self, resume_id: UUID, user_id: UUID, *, include_deleted: bool = False
    ) -> ResumeVersion | None:
        statement = select(ResumeVersion).where(
            ResumeVersion.id == resume_id,
            ResumeVersion.user_id == user_id,
        )
        if not include_deleted:
            statement = statement.where(ResumeVersion.deleted_at.is_(None))
        result = await self._session.execute(
            statement
        )
        return result.scalar_one_or_none()

    async def resume_by_key(self, user_id: UUID, key: str) -> ResumeVersion | None:
        result = await self._session.execute(
            select(ResumeVersion).where(
                ResumeVersion.user_id == user_id, ResumeVersion.idempotency_key == key
            )
        )
        return result.scalar_one_or_none()

    async def list_resumes(self, user_id: UUID) -> list[ResumeVersion]:
        rows = await self._session.scalars(
            select(ResumeVersion)
            .where(ResumeVersion.user_id == user_id, ResumeVersion.deleted_at.is_(None))
            .order_by(ResumeVersion.created_at.desc())
        )
        return list(rows)

    async def create_resume(self, row: ResumeVersion) -> ResumeVersion:
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def soft_delete_resume(self, row: ResumeVersion) -> None:
        row.deleted_at = datetime.now(UTC)
        await self._session.flush()

    async def get_job_target(
        self, target_id: UUID, user_id: UUID, *, include_deleted: bool = False
    ) -> JobTarget | None:
        statement = select(JobTarget).where(
            JobTarget.id == target_id,
            JobTarget.user_id == user_id,
        )
        if not include_deleted:
            statement = statement.where(JobTarget.deleted_at.is_(None))
        result = await self._session.execute(
            statement
        )
        return result.scalar_one_or_none()

    async def job_target_by_key(self, user_id: UUID, key: str) -> JobTarget | None:
        result = await self._session.execute(
            select(JobTarget).where(JobTarget.user_id == user_id, JobTarget.idempotency_key == key)
        )
        return result.scalar_one_or_none()

    async def list_job_targets(self, user_id: UUID) -> list[JobTarget]:
        rows = await self._session.scalars(
            select(JobTarget)
            .where(JobTarget.user_id == user_id, JobTarget.deleted_at.is_(None))
            .order_by(JobTarget.created_at.desc())
        )
        return list(rows)

    async def create_job_target(self, row: JobTarget) -> JobTarget:
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def soft_delete_job_target(self, row: JobTarget) -> None:
        row.deleted_at = datetime.now(UTC)
        await self._session.flush()

    async def assessment_by_key(self, user_id: UUID, key: str) -> ResumeAssessment | None:
        result = await self._session.execute(
            select(ResumeAssessment).where(
                ResumeAssessment.user_id == user_id,
                ResumeAssessment.idempotency_key == key,
            )
        )
        return result.scalar_one_or_none()

    async def get_assessment(
        self, assessment_id: UUID, user_id: UUID, *, for_update: bool = False
    ) -> ResumeAssessment | None:
        statement = select(ResumeAssessment).where(
                ResumeAssessment.id == assessment_id,
                ResumeAssessment.user_id == user_id,
            )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def assessment_by_source_run(
        self, source_run_id: UUID, user_id: UUID
    ) -> ResumeAssessment | None:
        result = await self._session.execute(
            select(ResumeAssessment).where(
                ResumeAssessment.source_run_id == source_run_id,
                ResumeAssessment.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def create_assessment(self, row: ResumeAssessment) -> ResumeAssessment:
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def list_assessments(self, user_id: UUID) -> list[ResumeAssessment]:
        rows = await self._session.scalars(
            select(ResumeAssessment)
            .where(ResumeAssessment.user_id == user_id)
            .order_by(ResumeAssessment.created_at.desc())
        )
        return list(rows)

    async def get_rewrite_decision(
        self,
        assessment_id: UUID,
        claim_id: str,
        user_id: UUID,
        *,
        for_update: bool = False,
    ) -> ResumeRewriteDecision | None:
        statement = select(ResumeRewriteDecision).where(
                ResumeRewriteDecision.assessment_id == assessment_id,
                ResumeRewriteDecision.claim_id == claim_id,
                ResumeRewriteDecision.user_id == user_id,
            )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def list_rewrite_decisions(
        self, assessment_id: UUID, user_id: UUID
    ) -> list[ResumeRewriteDecision]:
        rows = await self._session.scalars(
            select(ResumeRewriteDecision)
            .where(
                ResumeRewriteDecision.assessment_id == assessment_id,
                ResumeRewriteDecision.user_id == user_id,
            )
            .order_by(ResumeRewriteDecision.decided_at)
        )
        return list(rows)

    async def create_rewrite_decision(
        self, row: ResumeRewriteDecision
    ) -> ResumeRewriteDecision:
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row
