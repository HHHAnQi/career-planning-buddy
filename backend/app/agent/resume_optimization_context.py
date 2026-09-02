"""Build and freeze Resume optimization inputs from user-owned resources."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.resume_context_selection import (
    build_resume_context_manifest,
    hybrid_requirement_matches,
)
from app.core.database import session_transaction
from app.providers.embedding import EmbeddingProvider
from app.repositories.interviews import InterviewRepository
from app.repositories.resumes import ResumeRepository
from app.schemas.resumes import (
    JobRequirement,
    ResumeClaim,
    ResumeOptimizationInputSnapshot,
)
from app.services.resumes import stable_text_items


async def build_resume_optimization_context(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    user_id: UUID,
    interview_session_id: UUID | None,
    resume_version_id: UUID,
    job_target_id: UUID,
    embedding_provider: EmbeddingProvider,
    frozen_snapshot: dict[str, object] | None = None,
) -> ResumeOptimizationInputSnapshot:
    if frozen_snapshot is not None:
        return ResumeOptimizationInputSnapshot.model_validate(frozen_snapshot)
    async with session_factory() as session:
        async with session_transaction(session):
            interviews = InterviewRepository(session)
            materials = ResumeRepository(session)
            interview = (
                await interviews.get_session(interview_session_id, user_id)
                if interview_session_id is not None
                else None
            )
            if interview is not None and interview.report_status != "ready":
                raise ValueError("selected interview must be completed")
            resume = await materials.get_resume(
                resume_version_id, user_id, include_deleted=True
            )
            target = await materials.get_job_target(
                job_target_id, user_id, include_deleted=True
            )
            if resume is None or target is None:
                raise ValueError("frozen Resume or JobTarget is missing")
            if interview is not None and (
                interview.resume_version_id != resume.id
                or interview.job_target_id != target.id
            ):
                raise ValueError("interview does not belong to the frozen Resume/JD pair")
            turns = [
                item for item in (
                    await interviews.list_turns(interview.id, user_id)
                    if interview is not None
                    else []
                )
                if item.answer_status == "submitted" and item.answer_text
            ]
            raw_claims = resume.structured_json.get(
                "claims", stable_text_items(resume.source_text, prefix="claim")
            )
            raw_requirements = target.requirements_json.get(
                "requirements", stable_text_items(target.jd_text, prefix="req")
            )
            claims = [
                ResumeClaim.model_validate(item)
                for item in (raw_claims if isinstance(raw_claims, list) else [])
                if isinstance(item, dict)
            ]
            requirements = [
                JobRequirement.model_validate(item)
                for item in (
                    raw_requirements if isinstance(raw_requirements, list) else []
                )
                if isinstance(item, dict)
            ]
            evidence_turns: list[dict[str, object]] = [
                {
                    "turn_id": str(item.id),
                    "question_text": item.question_text,
                    "answer_text": item.answer_text,
                    "analysis_json": item.analysis_json,
                    "answered_at": item.answered_at.isoformat() if item.answered_at else None,
                }
                for item in turns
            ]
            matches = await hybrid_requirement_matches(
                claims, requirements, embedding_provider
            )
            manifest = build_resume_context_manifest(
                claims=claims,
                requirements=requirements,
                evidence_turns=evidence_turns,
                matches=matches,
                embedding_provider=embedding_provider.provider_name,
            )
            snapshot = ResumeOptimizationInputSnapshot(
                resume_version_id=resume.id,
                resume_label=resume.label,
                resume_text=resume.source_text,
                resume_hash=resume.content_hash,
                job_target_id=target.id,
                job_title=target.title,
                company=target.company,
                jd_text=target.jd_text,
                jd_hash=target.content_hash,
                interview_session_id=interview.id if interview is not None else None,
                assessment_mode=(
                    "evidence_enhanced" if interview is not None else "pre_interview"
                ),
                claims=claims,
                requirements=requirements,
                evidence_turns=evidence_turns,
                context_manifest=manifest,
                requirement_matches=matches,
            )
            from app.harness.snapshots import SnapshotService

            await SnapshotService.write_interview_input_once(
                session, run_id, snapshot.model_dump(mode="json")
            )
            return snapshot
