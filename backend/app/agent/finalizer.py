"""Single authority for Run terminal state and terminal event convergence."""

from datetime import UTC, datetime
from decimal import Decimal
from time import monotonic
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.errors import AgentLeaseLostError, PersistTransactionError, RunCancelledError
from app.core.database import session_transaction
from app.harness.budget import BudgetGuard
from app.harness.events import EventRecorder
from app.harness.evidence import evidence_refs_are_visible
from app.harness.trace import TraceRecorder
from app.models.agent_run import AgentRun, AgentStep
from app.models.interview import InterviewSession
from app.models.resume import ResumeAssessment
from app.repositories.plans import PlanRepository
from app.repositories.resumes import ResumeRepository
from app.schemas.agent_runs import (
    ClarificationRequest,
    CompanionMessageCandidate,
    EvidenceVisibility,
    NavigationResult,
    PlanCandidate,
    PlanResultSummary,
    SafeResponse,
)
from app.schemas.interviews import (
    InterviewAnswerCandidate,
    InterviewQuestionCandidate,
    InterviewReport,
    InterviewReportResultSummary,
    InterviewTurnResultSummary,
)
from app.schemas.resumes import (
    ResumeAssessmentResultSummary,
    ResumeOptimizationCandidate,
    ResumeOptimizationInputSnapshot,
)
from app.services.interview_persistence import InterviewPersistenceService

TERMINAL_STATUSES = {"completed", "degraded", "failed", "cancelled"}


class AgentRunFinalizer:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        budget: BudgetGuard | None,
        *,
        worker_id: str | None = None,
        attempt_count: int | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._budget = budget
        self._worker_id = worker_id
        self._attempt_count = attempt_count

    async def finalize_plan(
        self,
        *,
        run_id: UUID,
        user_id: UUID,
        candidate: PlanCandidate,
        evidence_visibility: EvidenceVisibility,
        companion: CompanionMessageCandidate,
        persist_step_id: UUID,
        fallback_reason: str | None,
        simulate_failure: bool = False,
    ) -> None:
        if self._budget is None:
            raise RuntimeError("plan finalization requires a valid RuntimeConfigSnapshot")
        started = monotonic()
        if not evidence_refs_are_visible(candidate.evidence_refs, evidence_visibility):
            raise PersistTransactionError(
                "candidate evidence refs are outside the final Provider call visibility"
            )
        try:
            async with self._session_factory() as session:
                async with session_transaction(session):
                    run = await self._lock_active_run(session, run_id)
                    self._ensure_can_persist(run)
                    plans = PlanRepository(session)
                    source_plan = None
                    if run.source_plan_id is not None:
                        source_plan = await plans.get_for_user(
                            run.source_plan_id, user_id, for_update=True
                        )
                    active_plan = await plans.get_active_for_user(user_id, for_update=True)
                    if source_plan is not None:
                        await plans.archive(source_plan)
                    if active_plan is not None and (
                        source_plan is None or active_plan.id != source_plan.id
                    ):
                        await plans.archive(active_plan)
                    if simulate_failure:
                        raise PersistTransactionError
                    provider = run.config_snapshot_json.get("provider")
                    provider_name = provider if isinstance(provider, str) else "unknown"
                    model_id = await session.scalar(
                        select(AgentStep.model_id)
                        .where(
                            AgentStep.run_id == run_id,
                            AgentStep.model_id.is_not(None),
                        )
                        .order_by(AgentStep.sequence.desc())
                        .limit(1)
                    )
                    plan = await plans.create_plan(
                        {
                            "user_id": user_id,
                            "source_run_id": run_id,
                            "parent_plan_id": source_plan.id if source_plan else None,
                            "status": "generated",
                            "plan_date": candidate.plan_date,
                            "horizon_start": candidate.horizon_start,
                            "horizon_end": candidate.horizon_end,
                            "overall_direction": candidate.overall_direction,
                            "weekly_focus_json": [
                                item.model_dump(mode="json") for item in candidate.weekly_focus
                            ],
                            "summary": candidate.summary,
                            "rationale": candidate.rationale,
                            "adjustment_reason": candidate.adjustment_reason,
                            "assumptions_json": candidate.assumptions,
                            "evidence_refs_json": [
                                item.model_dump(mode="json") for item in candidate.evidence_refs
                            ],
                            "metadata_json": {
                                "graph_version": run.graph_version,
                                "replan_mode": run.replan_mode,
                                "provider": provider_name,
                                "model_id": model_id,
                            },
                        }
                    )
                    tasks = await plans.create_tasks(
                        plan_id=plan.id,
                        user_id=user_id,
                        candidates=[
                            {
                                **task.model_dump(mode="python"),
                                "task_type": task.task_type.value,
                                "state": "pending",
                            }
                            for task in candidate.tasks
                        ],
                    )
                    await plans.create_companion(
                        user_id=user_id,
                        run_id=run_id,
                        plan_id=plan.id,
                        trigger_tag=companion.trigger_tag,
                        message=companion.message,
                        template_version=companion.template_version,
                    )
                    persist_step = await session.get(AgentStep, persist_step_id)
                    if persist_step is None:
                        raise PersistTransactionError("persist step is missing")
                    latency_ms = int((monotonic() - started) * 1000)
                    await TraceRecorder(session).complete_step(
                        persist_step,
                        status="completed",
                        latency_ms=latency_ms,
                        trace_data={"plan_id": str(plan.id), "task_count": len(tasks)},
                    )
                    recorder = EventRecorder(session)
                    await recorder.record(
                        run_id,
                        "node.completed",
                        {
                            "node_name": "persist",
                            "step_sequence": persist_step.sequence,
                            "status": "completed",
                            "latency_ms": latency_ms,
                        },
                    )
                    await recorder.record(
                        run_id,
                        "companion.message",
                        {
                            "trigger_tag": companion.trigger_tag,
                            "message": companion.message,
                        },
                    )
                    degraded = fallback_reason is not None
                    await recorder.record(
                        run_id,
                        "plan.ready",
                        {
                            "plan_id": str(plan.id),
                            "task_count": len(tasks),
                            "degraded": degraded,
                        },
                    )
                    result = PlanResultSummary(
                        plan_id=plan.id,
                        status="generated",
                        plan_date=plan.plan_date,
                        horizon_end=plan.horizon_end,
                        summary=plan.summary,
                        task_count=len(tasks),
                    )
                    status = "degraded" if degraded else "completed"
                    run.status = status
                    self._clear_lease(run)
                    run.result_kind = "plan"
                    run.result_payload_json = result.model_dump(mode="json")
                    run.final_plan_id = plan.id
                    run.fallback_reason = fallback_reason
                    run.error_code = None
                    run.error_message = None
                    run.total_tokens_in = self._budget.tokens_in
                    run.total_tokens_out = self._budget.tokens_out
                    model_steps = list(
                        (
                            await session.scalars(
                                select(AgentStep)
                                .where(
                                    AgentStep.run_id == run_id,
                                    AgentStep.model_id.is_not(None),
                                )
                                .order_by(AgentStep.sequence)
                            )
                        ).all()
                    )
                    if model_steps:
                        run.model_id = model_steps[-1].model_id
                        run.total_cost_cny = sum(
                            (item.cost_cny for item in model_steps),
                            start=Decimal("0"),
                        )
                    run.model_id = model_id
                    run.total_latency_ms = max(
                        0,
                        int((datetime.now(UTC) - run.created_at).total_seconds() * 1000),
                    )
                    run.finished_at = datetime.now(UTC)
                    event_type = "run.degraded" if degraded else "run.completed"
                    terminal_payload: dict[str, object] = {
                        "status": status,
                        "result_kind": "plan",
                        "final_plan_id": str(plan.id),
                    }
                    if fallback_reason is not None:
                        terminal_payload["fallback_reason"] = fallback_reason
                    await recorder.record(
                        run_id,
                        event_type,
                        terminal_payload,
                        allow_terminal_run=True,
                    )
        except (AgentLeaseLostError, RunCancelledError):
            raise
        except Exception:
            await self.finalize_failed(
                run_id,
                error_code="PERSIST_TRANSACTION_FAILED",
                persist_step_id=persist_step_id,
            )
            raise

    async def finalize_interview(
        self,
        *,
        run_id: UUID,
        candidate: InterviewQuestionCandidate | InterviewAnswerCandidate | InterviewReport,
        persist_step_id: UUID,
    ) -> None:
        if self._budget is None:
            raise RuntimeError("interview finalization requires a valid RuntimeConfigSnapshot")
        started = monotonic()
        try:
            async with self._session_factory() as session:
                async with session_transaction(session):
                    run = await self._lock_active_run(session, run_id)
                    self._ensure_can_persist(run)
                    interview = await session.scalar(
                        select(InterviewSession)
                        .where(
                            InterviewSession.id == run.interview_session_id,
                            InterviewSession.user_id == run.user_id,
                        )
                        .with_for_update()
                    )
                    if interview is None:
                        raise PersistTransactionError("InterviewSession is missing")
                    persistence = InterviewPersistenceService(session)
                    event_type: str
                    event_payload: dict[str, object]
                    start_report = False
                    result: InterviewTurnResultSummary | InterviewReportResultSummary
                    if isinstance(candidate, InterviewQuestionCandidate):
                        next_turn = await persistence.persist_question(
                            run=run,
                            interview=interview,
                            candidate=candidate,
                        )
                        result = InterviewTurnResultSummary(
                            interview_id=interview.id,
                            turn_id=next_turn.id,
                            session_status="active",
                            next_turn_id=next_turn.id,
                        )
                        event_type = "interview.turn.ready"
                        event_payload = {
                            "interview_id": str(interview.id),
                            "turn_id": str(next_turn.id),
                        }
                        result_kind = "interview_turn"
                    elif isinstance(candidate, InterviewAnswerCandidate):
                        current_turn, following_turn = await persistence.persist_answer(
                            run=run,
                            interview=interview,
                            candidate=candidate,
                        )
                        result = InterviewTurnResultSummary(
                            interview_id=interview.id,
                            turn_id=current_turn.id,
                            session_status=(
                                "report_generating"
                                if candidate.next_action == "finish"
                                else "active"
                            ),
                            next_turn_id=(
                                following_turn.id if following_turn is not None else None
                            ),
                        )
                        event_type = "interview.answer.ready"
                        event_payload = {
                            "interview_id": str(interview.id),
                            "turn_id": str(current_turn.id),
                            "next_turn_id": (
                                str(following_turn.id) if following_turn is not None else None
                            ),
                            "next_action": candidate.next_action,
                        }
                        start_report = candidate.next_action == "finish"
                        result_kind = "interview_turn"
                    else:
                        if not isinstance(candidate, InterviewReport):
                            raise PersistTransactionError(
                                "interview candidate does not match the Run kind"
                            )
                        await persistence.persist_report(
                            run=run,
                            interview=interview,
                            report=candidate,
                        )
                        result = InterviewReportResultSummary(
                            interview_id=interview.id,
                            report_version=interview.report_version or 1,
                        )
                        event_type = "interview.report.ready"
                        event_payload = {
                            "interview_id": str(interview.id),
                            "report_version": interview.report_version or 1,
                        }
                        result_kind = "interview_report"
                    persist_step = await session.get(AgentStep, persist_step_id)
                    if persist_step is None:
                        raise PersistTransactionError("interview persist step is missing")
                    latency_ms = int((monotonic() - started) * 1000)
                    await TraceRecorder(session).complete_step(
                        persist_step,
                        status="completed",
                        latency_ms=latency_ms,
                        trace_data=event_payload,
                    )
                    recorder = EventRecorder(session)
                    await recorder.record(
                        run_id,
                        "node.completed",
                        {
                            "node_name": "interview_persist",
                            "step_sequence": persist_step.sequence,
                            "status": "completed",
                            "latency_ms": latency_ms,
                        },
                    )
                    await recorder.record(run_id, event_type, event_payload)
                    run.status = "completed"
                    self._clear_lease(run)
                    run.result_kind = result_kind
                    run.result_payload_json = result.model_dump(mode="json")
                    run.fallback_reason = None
                    run.error_code = None
                    run.error_message = None
                    run.total_tokens_in = self._budget.tokens_in
                    run.total_tokens_out = self._budget.tokens_out
                    model_steps = list(
                        (
                            await session.scalars(
                                select(AgentStep)
                                .where(
                                    AgentStep.run_id == run_id,
                                    AgentStep.model_id.is_not(None),
                                )
                                .order_by(AgentStep.sequence)
                            )
                        ).all()
                    )
                    if model_steps:
                        run.model_id = model_steps[-1].model_id
                        run.total_cost_cny = sum(
                            (item.cost_cny for item in model_steps),
                            start=Decimal("0"),
                        )
                    run.finished_at = datetime.now(UTC)
                    run.total_latency_ms = max(
                        0,
                        int((run.finished_at - run.created_at).total_seconds() * 1000),
                    )
                    await recorder.record(
                        run_id,
                        "run.completed",
                        {"status": "completed", "result_kind": result_kind},
                        allow_terminal_run=True,
                    )
                    if start_report:
                        await session.flush()
                        await persistence.create_auto_report_run(
                            source_run=run, interview=interview
                        )
        except (AgentLeaseLostError, RunCancelledError):
            raise
        except Exception:
            await self.finalize_failed(
                run_id,
                error_code="PERSIST_TRANSACTION_FAILED",
                persist_step_id=persist_step_id,
            )
            raise

    async def finalize_resume_optimization(
        self,
        *,
        run_id: UUID,
        candidate: ResumeOptimizationCandidate,
        snapshot: ResumeOptimizationInputSnapshot,
        persist_step_id: UUID,
    ) -> None:
        """Atomically persist the evidence-bound candidate and terminal Run event."""
        if self._budget is None:
            raise RuntimeError("Resume finalization requires a valid RuntimeConfigSnapshot")
        started = monotonic()
        try:
            async with self._session_factory() as session:
                async with session_transaction(session):
                    run = await self._lock_active_run(session, run_id)
                    self._ensure_can_persist(run)
                    materials = ResumeRepository(session)
                    existing = await materials.assessment_by_source_run(run_id, run.user_id)
                    if existing is None:
                        assessment = await materials.create_assessment(
                            ResumeAssessment(
                                user_id=run.user_id,
                                resume_version_id=snapshot.resume_version_id,
                                job_target_id=snapshot.job_target_id,
                                interview_session_id=snapshot.interview_session_id,
                                source_run_id=run.id,
                                findings_json=[
                                    item.model_dump(mode="json") for item in candidate.claims
                                ],
                                limitations_json=candidate.limitations,
                                context_manifest_json=(
                                    snapshot.context_manifest.model_dump(mode="json")
                                ),
                                idempotency_key=f"run-{run.id}",
                                request_hash=snapshot.resume_hash,
                            )
                        )
                    else:
                        assessment = existing
                    persist_step = await session.get(AgentStep, persist_step_id)
                    if persist_step is None:
                        raise PersistTransactionError("Resume persist step is missing")
                    latency_ms = int((monotonic() - started) * 1000)
                    trace = {
                        "assessment_id": str(assessment.id),
                        "claim_count": len(candidate.claims),
                        "selected_evidence_count": len(
                            snapshot.context_manifest.selected_evidence_refs
                        ),
                    }
                    await TraceRecorder(session).complete_step(
                        persist_step,
                        status="completed",
                        latency_ms=latency_ms,
                        trace_data=trace,
                    )
                    recorder = EventRecorder(session)
                    await recorder.record(
                        run_id,
                        "node.completed",
                        {
                            "node_name": "resume_candidate_persist",
                            "step_sequence": persist_step.sequence,
                            "status": "completed",
                            "latency_ms": latency_ms,
                        },
                    )
                    await recorder.record(run_id, "resume.optimization.ready", trace)
                    result = ResumeAssessmentResultSummary(
                        assessment_id=assessment.id,
                        claim_count=len(candidate.claims),
                    )
                    run.status = "completed"
                    self._clear_lease(run)
                    run.result_kind = "resume_optimization"
                    run.result_payload_json = result.model_dump(mode="json")
                    run.fallback_reason = None
                    run.error_code = None
                    run.error_message = None
                    run.total_tokens_in = self._budget.tokens_in
                    run.total_tokens_out = self._budget.tokens_out
                    model_steps = list(
                        (
                            await session.scalars(
                                select(AgentStep)
                                .where(
                                    AgentStep.run_id == run_id,
                                    AgentStep.model_id.is_not(None),
                                )
                                .order_by(AgentStep.sequence)
                            )
                        ).all()
                    )
                    if model_steps:
                        run.model_id = model_steps[-1].model_id
                        run.total_cost_cny = sum(
                            (item.cost_cny for item in model_steps),
                            start=Decimal("0"),
                        )
                    run.finished_at = datetime.now(UTC)
                    run.total_latency_ms = max(
                        0,
                        int((run.finished_at - run.created_at).total_seconds() * 1000),
                    )
                    await recorder.record(
                        run_id,
                        "run.completed",
                        {
                            "status": "completed",
                            "result_kind": "resume_optimization",
                            "assessment_id": str(assessment.id),
                        },
                        allow_terminal_run=True,
                    )
        except (AgentLeaseLostError, RunCancelledError):
            raise
        except Exception:
            await self.finalize_failed(
                run_id,
                error_code="PERSIST_TRANSACTION_FAILED",
                persist_step_id=persist_step_id,
            )
            raise

    async def finalize_degraded(
        self,
        *,
        run_id: UUID,
        result_kind: str,
        result: ClarificationRequest | SafeResponse | NavigationResult,
        fallback_reason: str,
    ) -> None:
        async with self._session_factory() as session:
            async with session_transaction(session):
                run = await self._lock_active_run(session, run_id)
                self._ensure_can_persist(run)
                recorder = EventRecorder(session)
                if isinstance(result, ClarificationRequest):
                    await recorder.record(
                        run_id,
                        "clarification.requested",
                        result.model_dump(mode="json"),
                    )
                elif isinstance(result, NavigationResult):
                    await recorder.record(
                        run_id,
                        "navigation.suggested",
                        result.model_dump(mode="json"),
                    )
                run.status = "degraded"
                self._clear_lease(run)
                run.result_kind = result_kind
                run.result_payload_json = result.model_dump(mode="json")
                run.final_plan_id = None
                run.fallback_reason = fallback_reason
                run.error_code = None
                run.finished_at = datetime.now(UTC)
                run.total_latency_ms = max(
                    0,
                    int((run.finished_at - run.created_at).total_seconds() * 1000),
                )
                await InterviewPersistenceService(session).mark_unsuccessful(run)
                await recorder.record(
                    run_id,
                    "run.degraded",
                    {
                        "status": "degraded",
                        "result_kind": result_kind,
                        "fallback_reason": fallback_reason,
                        "final_plan_id": None,
                    },
                    allow_terminal_run=True,
                )

    async def finalize_failed(
        self,
        run_id: UUID,
        *,
        error_code: str,
        persist_step_id: UUID | None = None,
    ) -> bool:
        return await self._finalize_without_result(
            run_id,
            status="failed",
            event_type="run.failed",
            error_code=error_code,
            persist_step_id=persist_step_id,
        )

    async def finalize_cancelled(self, run_id: UUID) -> bool:
        return await self._finalize_without_result(
            run_id,
            status="cancelled",
            event_type="run.cancelled",
            error_code="RUN_CANCELLED",
        )

    async def _finalize_without_result(
        self,
        run_id: UUID,
        *,
        status: str,
        event_type: str,
        error_code: str,
        persist_step_id: UUID | None = None,
    ) -> bool:
        async with self._session_factory() as session:
            async with session_transaction(session):
                run = await session.scalar(
                    select(AgentRun).where(AgentRun.id == run_id).with_for_update()
                )
                if run is None or run.status in TERMINAL_STATUSES:
                    return False
                self._assert_lease_owner(run)
                recorder = EventRecorder(session)
                if persist_step_id is not None:
                    step = await session.get(AgentStep, persist_step_id)
                    if step is not None and step.status == "running":
                        await TraceRecorder(session).complete_step(
                            step,
                            status="failed",
                            latency_ms=0,
                            trace_data={},
                            error_code=error_code,
                            error_message=error_code,
                        )
                        await recorder.record(
                            run_id,
                            "node.completed",
                            {
                                "node_name": step.node_name,
                                "step_sequence": step.sequence,
                                "status": "failed",
                                "latency_ms": 0,
                            },
                        )
                run.status = status
                self._clear_lease(run)
                run.result_kind = None
                run.result_payload_json = None
                run.final_plan_id = None
                run.fallback_reason = None
                run.error_code = error_code
                run.error_message = error_code
                run.finished_at = datetime.now(UTC)
                run.total_latency_ms = max(
                    0,
                    int((run.finished_at - run.created_at).total_seconds() * 1000),
                )
                await InterviewPersistenceService(session).mark_unsuccessful(run)
                await recorder.record(
                    run_id,
                    event_type,
                    {"status": status, "error_code": error_code},
                    allow_terminal_run=True,
                )
                return True

    @staticmethod
    def _clear_lease(run: AgentRun) -> None:
        run.worker_id = None
        run.lease_expires_at = None
        run.heartbeat_at = None

    async def _lock_active_run(self, session: AsyncSession, run_id: UUID) -> AgentRun:
        run = await session.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
        if run is None or run.status in TERMINAL_STATUSES:
            raise RuntimeError("Run is already terminal")
        self._assert_lease_owner(run)
        return run

    def _assert_lease_owner(self, run: AgentRun) -> None:
        if self._worker_id is None:
            return
        if (
            run.status != "running"
            or run.worker_id != self._worker_id
            or (
                self._attempt_count is not None
                and run.attempt_count != self._attempt_count
            )
            or run.lease_expires_at is None
            or run.lease_expires_at <= datetime.now(UTC)
        ):
            raise AgentLeaseLostError("Agent Run lease ownership was lost")

    @staticmethod
    def _ensure_can_persist(run: AgentRun) -> None:
        if run.status != "running":
            raise PersistTransactionError("Run must be running")
        if run.cancel_requested_at is not None:
            raise RunCancelledError("Agent Run cancellation was requested")
        if datetime.now(UTC) >= run.deadline_at:
            raise PersistTransactionError("Run deadline was exceeded")
