"""Evidence-bounded Resume Claim Validation use case."""

import json
import re
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from http import HTTPStatus
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.executor import AgentRunExecutor
from app.core.config import Settings
from app.core.database import session_transaction
from app.core.exceptions import AppError
from app.harness.events import EventRecorder
from app.harness.runtime_bundle import (
    build_resume_runtime_bundle,
    get_or_create_runtime_bundle,
)
from app.harness.snapshots import SnapshotService
from app.models.agent_run import AgentRun
from app.models.interview import InterviewTurn
from app.models.resume import ResumeAssessment, ResumeRewriteDecision, ResumeVersion
from app.repositories.interviews import InterviewRepository
from app.repositories.resumes import ResumeRepository
from app.schemas.interviews import TurnAnalysis
from app.schemas.resumes import (
    JobRequirement,
    ResumeAssessmentCreateRequest,
    ResumeAssessmentResponse,
    ResumeClaim,
    ResumeClaimFinding,
    ResumeOptimizationRunResponse,
    ResumeRewriteApplyResponse,
    ResumeRewriteBatchApplyRequest,
    ResumeRewriteBatchApplyResponse,
    ResumeRewriteDecisionRequest,
    ResumeRewriteDecisionResponse,
    ResumeVersionResponse,
)
from app.services.resumes import stable_text_items


class ResumeAssessmentService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings | None = None,
        executor: AgentRunExecutor | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._executor = executor
        self._materials = ResumeRepository(session)
        self._interviews = InterviewRepository(session)

    async def optimize(
        self,
        *,
        user_id: UUID,
        payload: ResumeAssessmentCreateRequest,
        idempotency_key: str,
    ) -> ResumeOptimizationRunResponse:
        """Create a durable asynchronous Agent Run after validating frozen ownership."""
        if self._settings is None or self._executor is None:
            raise RuntimeError("Resume Agent runtime dependencies are not configured")
        config = SnapshotService.build_resume_optimization_config(self._settings)
        created = False
        try:
            async with session_transaction(self._session):
                existing = await self._session.scalar(
                    select(AgentRun).where(
                        AgentRun.user_id == user_id,
                        AgentRun.idempotency_key == idempotency_key,
                    )
                )
                if existing is not None:
                    if (
                        existing.run_kind != "resume_optimization"
                        or existing.interview_session_id != payload.interview_session_id
                        or existing.resume_version_id != payload.resume_version_id
                        or existing.job_target_id != payload.job_target_id
                    ):
                        raise AppError(
                            code="STATE_IDEMPOTENCY_KEY_REUSED",
                            message="Idempotency-Key was already used with another request",
                            status_code=HTTPStatus.CONFLICT,
                        )
                    run = existing
                else:
                    resume = await self._materials.get_resume(
                        payload.resume_version_id, user_id, include_deleted=True
                    )
                    target = await self._materials.get_job_target(
                        payload.job_target_id, user_id, include_deleted=True
                    )
                    interview = (
                        await self._interviews.get_session(payload.interview_session_id, user_id)
                        if payload.interview_session_id is not None
                        else None
                    )
                    if resume is None or target is None:
                        raise _not_found()
                    if interview is not None and (
                        interview.resume_version_id != resume.id
                        or interview.job_target_id != target.id
                        or interview.report_status != "ready"
                    ):
                        raise AppError(
                            code="STATE_RESUME_ASSESSMENT_INVALID",
                            message=(
                                "optimization requires the completed interview "
                                "for this Resume/JD pair"
                            ),
                            status_code=HTTPStatus.CONFLICT,
                        )
                    runtime_bundle = await get_or_create_runtime_bundle(
                        self._session,
                        build_resume_runtime_bundle(self._settings, config),
                    )
                    run = AgentRun(
                        user_id=user_id,
                        run_kind="resume_optimization",
                        interview_session_id=interview.id if interview is not None else None,
                        resume_version_id=resume.id,
                        job_target_id=target.id,
                        runtime_bundle_id=runtime_bundle.id,
                        idempotency_key=idempotency_key,
                        request_text=(
                            "Optimize frozen Resume claims against JD with bounded evidence"
                        ),
                        hint_intent="resume_optimization",
                        resolved_intent="resume_optimization",
                        status="pending",
                        graph_version=config.graph_version,
                        config_snapshot_json=config.model_dump(mode="json"),
                        deadline_at=datetime.now(UTC)
                        + timedelta(seconds=config.deadline_seconds),
                    )
                    self._session.add(run)
                    await self._session.flush()
                    await EventRecorder(self._session).record(
                        run.id,
                        "run.created",
                        {
                            "status": "pending",
                            "graph_version": run.graph_version,
                            "run_kind": "resume_optimization",
                            "runtime_bundle_hash": runtime_bundle.bundle_hash,
                            "assessment_mode": (
                                "evidence_enhanced" if interview is not None else "pre_interview"
                            ),
                        },
                    )
                    created = True
        except IntegrityError as exc:
            raise AppError(
                code="STATE_RUN_ALREADY_ACTIVE",
                message="another Agent Run is already active",
                status_code=HTTPStatus.CONFLICT,
            ) from exc
        if created:
            self._executor.submit(run.id)
        return ResumeOptimizationRunResponse(
            run_id=run.id,
            events_url=f"/api/v1/agent-runs/{run.id}/events",
        )

    async def create(
        self,
        *,
        user_id: UUID,
        payload: ResumeAssessmentCreateRequest,
        idempotency_key: str,
    ) -> ResumeAssessmentResponse:
        if payload.interview_session_id is None:
            raise AppError(
                code="STATE_LEGACY_ASSESSMENT_REQUIRES_INTERVIEW",
                message=(
                    "the legacy synchronous assessment requires an interview; "
                    "use /resume-assessments/optimize for pre-interview diagnosis"
                ),
                status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            )
        request_hash = _hash(payload.model_dump(mode="json"))
        async with session_transaction(self._session):
            existing = await self._materials.assessment_by_key(user_id, idempotency_key)
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise AppError(
                        code="STATE_IDEMPOTENCY_KEY_REUSED",
                        message="Idempotency-Key was already used with another request",
                        status_code=HTTPStatus.CONFLICT,
                    )
                return await self._response(existing)
            resume = await self._materials.get_resume(
                payload.resume_version_id, user_id, include_deleted=True
            )
            target = await self._materials.get_job_target(
                payload.job_target_id, user_id, include_deleted=True
            )
            interview = await self._interviews.get_session(
                payload.interview_session_id, user_id
            )
            if resume is None or target is None or interview is None:
                raise _not_found()
            if (
                interview.resume_version_id != resume.id
                or interview.job_target_id != target.id
                or interview.report_status != "ready"
            ):
                raise AppError(
                    code="STATE_RESUME_ASSESSMENT_INVALID",
                    message="assessment requires the completed interview for this Resume/JD pair",
                    status_code=HTTPStatus.CONFLICT,
                )
            turns = [
                turn
                for turn in await self._interviews.list_turns(interview.id, user_id)
                if turn.answer_status == "submitted"
            ]
            if not turns:
                raise AppError(
                    code="STATE_RESUME_ASSESSMENT_INVALID",
                    message="assessment requires at least one submitted interview answer",
                    status_code=HTTPStatus.CONFLICT,
                )
            claims = _claims(resume.structured_json, resume.source_text)
            requirements = _requirements(target.requirements_json, target.jd_text)
            findings = [_finding(claim, requirements, turns) for claim in claims]
            row = await self._materials.create_assessment(
                ResumeAssessment(
                    user_id=user_id,
                    resume_version_id=resume.id,
                    job_target_id=target.id,
                    interview_session_id=interview.id,
                    findings_json=[item.model_dump(mode="json") for item in findings],
                    limitations_json=[
                        "结论只依据所选简历、JD 与该场面试原回答，不代表背景调查。",
                        "insufficient_evidence 表示当前证据不足，不等同于主张错误。",
                    ],
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                )
            )
            run = AgentRun(
                user_id=user_id,
                run_kind="resume_assessment",
                interview_session_id=interview.id,
                idempotency_key=f"resume-assessment-{row.id}"[:64],
                request_text="Validate Resume claims against saved interview evidence",
                hint_intent="resume_assessment",
                resolved_intent="resume_assessment",
                status="completed",
                result_kind="resume_assessment",
                result_payload_json={
                    "assessment_id": str(row.id),
                    "claim_count": len(findings),
                },
                graph_version="career-coach-v2-batch3-sync",
                config_snapshot_json={
                    "execution": "deterministic_evidence_validation",
                    "provider_calls": 0,
                },
                deadline_at=row.created_at,
                finished_at=row.created_at,
            )
            self._session.add(run)
            await self._session.flush()
            await EventRecorder(self._session).record(
                run.id,
                "run.completed",
                {
                    "status": "completed",
                    "result_kind": "resume_assessment",
                    "assessment_id": str(row.id),
                },
                allow_terminal_run=True,
            )
            return await self._response(row)

    async def get(self, assessment_id: UUID, user_id: UUID) -> ResumeAssessmentResponse:
        async with session_transaction(self._session):
            row = await self._materials.get_assessment(assessment_id, user_id)
            if row is None:
                raise _not_found()
            return await self._response(row)

    async def list(self, user_id: UUID) -> list[ResumeAssessmentResponse]:
        async with session_transaction(self._session):
            rows = await self._materials.list_assessments(user_id)
            return [await self._response(row) for row in rows]

    async def decide_rewrite(
        self,
        *,
        assessment_id: UUID,
        claim_id: str,
        user_id: UUID,
        payload: ResumeRewriteDecisionRequest,
    ) -> ResumeRewriteDecisionResponse:
        async with session_transaction(self._session):
            assessment = await self._materials.get_assessment(assessment_id, user_id)
            if assessment is None:
                raise _not_found()
            existing = await self._materials.get_rewrite_decision(
                assessment_id, claim_id, user_id
            )
            if existing is not None:
                requested_text = payload.rewrite_text.strip() if payload.rewrite_text else None
                if existing.status == payload.status and existing.rewrite_text == requested_text:
                    return _decision_response(existing)
                raise AppError(
                    code="STATE_RESUME_REWRITE_ALREADY_DECIDED",
                    message="this rewrite already has a human decision",
                    status_code=HTTPStatus.CONFLICT,
                )
            finding = _finding_by_id(assessment, claim_id)
            if finding.suggested_rewrite is None:
                raise AppError(
                    code="STATE_RESUME_REWRITE_UNAVAILABLE",
                    message="this supported claim has no rewrite suggestion",
                    status_code=HTTPStatus.CONFLICT,
                )
            row = await self._materials.create_rewrite_decision(
                ResumeRewriteDecision(
                    user_id=user_id,
                    assessment_id=assessment.id,
                    claim_id=claim_id,
                    status=payload.status,
                    original_suggestion=finding.suggested_rewrite,
                    rewrite_text=(payload.rewrite_text.strip() if payload.rewrite_text else None),
                )
            )
            return _decision_response(row)

    async def apply_rewrite(
        self, *, assessment_id: UUID, claim_id: str, user_id: UUID
    ) -> ResumeRewriteApplyResponse:
        """Deprecated compatibility wrapper over the canonical batch transaction."""
        applied = await self.apply_rewrites_batch(
            assessment_id=assessment_id,
            user_id=user_id,
            payload=ResumeRewriteBatchApplyRequest(claim_ids=[claim_id]),
        )
        return ResumeRewriteApplyResponse(
            decision=applied.decisions[0],
            resume_version=applied.resume_version,
        )

    async def apply_rewrites_batch(
        self,
        *,
        assessment_id: UUID,
        user_id: UUID,
        payload: ResumeRewriteBatchApplyRequest,
    ) -> ResumeRewriteBatchApplyResponse:
        """Merge accepted non-overlapping rewrites into exactly one child version."""
        async with session_transaction(self._session):
            assessment = await self._materials.get_assessment(
                assessment_id, user_id, for_update=True
            )
            if assessment is None:
                raise _not_found()
            decisions = []
            findings = []
            for claim_id in payload.claim_ids:
                decision = await self._materials.get_rewrite_decision(
                    assessment_id, claim_id, user_id, for_update=True
                )
                if decision is None or decision.status not in {"accepted", "applied"}:
                    raise AppError(
                        code="STATE_RESUME_REWRITE_NOT_ACCEPTED",
                        message="all selected rewrites must be accepted before applying",
                        status_code=HTTPStatus.CONFLICT,
                    )
                decisions.append(decision)
                findings.append(_finding_by_id(assessment, claim_id))
            applied_ids = {
                item.applied_resume_version_id
                for item in decisions
                if item.status == "applied"
            }
            if applied_ids:
                if len(applied_ids) != 1 or any(item.status != "applied" for item in decisions):
                    raise AppError(
                        code="STATE_RESUME_REWRITE_BATCH_CONFLICT",
                        message="selected rewrites were already applied in different versions",
                        status_code=HTTPStatus.CONFLICT,
                    )
                version = await self._materials.get_resume(
                    next(iter(applied_ids)), user_id, include_deleted=True  # type: ignore[arg-type]
                )
                if version is None:
                    raise _not_found()
                return ResumeRewriteBatchApplyResponse(
                    decisions=[_decision_response(item) for item in decisions],
                    resume_version=_resume_response(version),
                )
            source = await self._materials.get_resume(
                assessment.resume_version_id, user_id, include_deleted=True
            )
            if source is None:
                raise _not_found()
            replacements: list[tuple[int, int, str]] = []
            for finding, decision in zip(findings, decisions, strict=True):
                start, end = finding.source_start, finding.source_end
                if (
                    start is None
                    or end is None
                    or finding.source_hash is None
                    or end > len(source.source_text)
                    or source.source_text[start:end] != finding.claim_text
                    or sha256(source.source_text[start:end].encode()).hexdigest()
                    != finding.source_hash
                    or decision.rewrite_text is None
                ):
                    raise AppError(
                        code="STATE_RESUME_REWRITE_SOURCE_MISMATCH",
                        message="a selected claim no longer matches the frozen ResumeVersion",
                        status_code=HTTPStatus.CONFLICT,
                    )
                replacements.append(
                    (start, end, decision.rewrite_text)
                )
            replacements.sort()
            if any(
                current[0] < previous[1]
                for previous, current in zip(
                    replacements, replacements[1:], strict=False
                )
            ):
                raise AppError(
                    code="STATE_RESUME_REWRITE_OVERLAP",
                    message="selected claims overlap and cannot be merged safely",
                    status_code=HTTPStatus.CONFLICT,
                )
            rewritten = source.source_text
            for start, end, value in reversed(replacements):
                rewritten = f"{rewritten[:start]}{value}{rewritten[end:]}"
            fingerprint = _hash(
                {
                    "assessment_id": str(assessment.id),
                    "claim_ids": sorted(payload.claim_ids),
                    "rewrites": [item.rewrite_text for item in decisions],
                }
            )
            version = await self._materials.create_resume(
                ResumeVersion(
                    user_id=user_id,
                    label=f"{source.label} · AI 合并优化版",
                    source_type="pasted_text",
                    source_text=rewritten,
                    structured_json={"claims": stable_text_items(rewritten, prefix="claim")},
                    content_hash=sha256(rewritten.encode()).hexdigest(),
                    parent_version_id=source.id,
                    idempotency_key=f"rewrite-batch-{fingerprint[:50]}",
                    request_hash=fingerprint,
                )
            )
            now = datetime.now(UTC)
            for decision in decisions:
                decision.status = "applied"
                decision.applied_resume_version_id = version.id
                decision.applied_at = now
            await self._session.flush()
            return ResumeRewriteBatchApplyResponse(
                decisions=[_decision_response(item) for item in decisions],
                resume_version=_resume_response(version),
            )

    async def _response(self, row: ResumeAssessment) -> ResumeAssessmentResponse:
        decisions = await self._materials.list_rewrite_decisions(row.id, row.user_id)
        return ResumeAssessmentResponse(
            assessment_id=row.id,
            resume_version_id=row.resume_version_id,
            job_target_id=row.job_target_id,
            interview_session_id=row.interview_session_id,
            claims=[ResumeClaimFinding.model_validate(item) for item in row.findings_json],
            rewrite_decisions=[_decision_response(item) for item in decisions],
            source_run_id=row.source_run_id,
            context_manifest=(
                row.context_manifest_json if row.context_manifest_json else None
            ),
            limitations=list(row.limitations_json),
            created_at=row.created_at,
        )


def _claims(structured: dict[str, object], source_text: str) -> list[ResumeClaim]:
    raw = structured.get("claims")
    values = raw if isinstance(raw, list) else stable_text_items(source_text, prefix="claim")
    claims = [ResumeClaim.model_validate(value) for value in values if isinstance(value, dict)]
    if not claims:
        raise AppError(
            code="STATE_RESUME_ASSESSMENT_INVALID",
            message="resume contains no assessable claims",
            status_code=HTTPStatus.CONFLICT,
        )
    return claims


def _requirements(structured: dict[str, object], jd_text: str) -> list[JobRequirement]:
    raw = structured.get("requirements")
    values = raw if isinstance(raw, list) else stable_text_items(jd_text, prefix="req")
    return [JobRequirement.model_validate(value) for value in values if isinstance(value, dict)]


def _tokens(value: str) -> set[str]:
    return {
        token.casefold()
        for token in re.findall(r"[A-Za-z0-9+#.]{2,}|[\u4e00-\u9fff]{2,}", value)
    }


def _similarity(left: str, right: str) -> int:
    return len(_tokens(left) & _tokens(right))


def _finding(
    claim: ResumeClaim,
    requirements: list[JobRequirement],
    turns: list[InterviewTurn],
) -> ResumeClaimFinding:
    ranked_requirements = sorted(
        requirements, key=lambda item: _similarity(claim.text, item.text), reverse=True
    )
    linked_requirements = [
        item.requirement_id
        for item in ranked_requirements[:3]
        if _similarity(claim.text, item.text)
    ]
    ranked_turns = sorted(
        turns,
        key=lambda turn: _similarity(
            claim.text,
            f"{turn.question_text} {turn.answer_text or ''}",
        ),
        reverse=True,
    )
    relevant = [
        turn
        for turn in ranked_turns[:3]
        if _similarity(
            claim.text,
            f"{turn.question_text} {turn.answer_text or ''}",
        )
    ]
    evidence_turns = relevant or ranked_turns[:1]
    explicit_error = False
    for turn in evidence_turns:
        raw_analysis = turn.analysis_json
        if not isinstance(raw_analysis, dict):
            continue
        analysis = TurnAnalysis.model_validate(raw_analysis)
        explicit_error = explicit_error or any(
            finding.verdict == "incorrect"
            and (
                _similarity(claim.text, finding.claim) > 0
                or str(turn.id) in finding.evidence_refs
            )
            for finding in analysis.factual_findings
        )
    answer_support = max(
        (_similarity(claim.text, turn.answer_text or "") for turn in relevant),
        default=0,
    )
    if explicit_error:
        verdict = "unsupported"
        rationale = "面试逐题分析对该主张给出了可回溯的明确错误证据。"
    elif answer_support >= 2 and linked_requirements:
        verdict = "supported"
        rationale = "面试回答提供了与该主张及目标岗位要求一致的具体证据。"
    elif answer_support >= 1:
        verdict = "partially_supported"
        rationale = "面试回答触及该主张，但缺少足够细节或结果证据。"
    else:
        verdict = "insufficient_evidence"
        rationale = "该场面试没有提供足够证据验证此主张；这不表示主张错误。"
    rewrite = None
    if verdict != "supported":
        rewrite = f"{claim.text}（补充本人行动、技术取舍与可验证结果后再使用）"
    return ResumeClaimFinding(
        claim_id=claim.claim_id,
        claim_text=claim.text,
        verdict=verdict,
        rationale=rationale,
        requirement_ids=linked_requirements,
        evidence_turn_ids=[turn.id for turn in evidence_turns],
        suggested_rewrite=rewrite,
        source_start=claim.source_start,
        source_end=claim.source_end,
        source_hash=claim.source_hash,
    )


def _finding_by_id(row: ResumeAssessment, claim_id: str) -> ResumeClaimFinding:
    for value in row.findings_json:
        finding = ResumeClaimFinding.model_validate(value)
        if finding.claim_id == claim_id:
            return finding
    raise AppError(
        code="NOT_FOUND_RESUME_CLAIM",
        message="resume claim was not found in this assessment",
        status_code=HTTPStatus.NOT_FOUND,
    )


def _decision_response(row: ResumeRewriteDecision) -> ResumeRewriteDecisionResponse:
    return ResumeRewriteDecisionResponse(
        assessment_id=row.assessment_id,
        claim_id=row.claim_id,
        status=row.status,
        original_suggestion=row.original_suggestion,
        rewrite_text=row.rewrite_text,
        applied_resume_version_id=row.applied_resume_version_id,
        decided_at=row.decided_at,
        applied_at=row.applied_at,
    )


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


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _not_found() -> AppError:
    return AppError(
        code="NOT_FOUND_RESUME_ASSESSMENT",
        message="resume assessment resource was not found",
        status_code=HTTPStatus.NOT_FOUND,
    )
