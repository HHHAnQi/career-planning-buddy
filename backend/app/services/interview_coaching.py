"""Batch 2 report-to-memory, training confirmation, retest, and comparison use cases."""

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from http import HTTPStatus
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.executor import AgentRunExecutor
from app.core.config import Settings
from app.core.database import session_transaction
from app.core.exceptions import AppError
from app.harness.events import EventRecorder
from app.harness.snapshots import SnapshotService
from app.models.agent_run import AgentRun
from app.models.interview import InterviewSession
from app.models.plan import Plan, Task
from app.repositories.agent_runs import AgentRunRepository
from app.repositories.interviews import InterviewRepository
from app.repositories.memories import MemoryRepository
from app.repositories.plans import PlanRepository
from app.repositories.profiles import ProfileRepository
from app.repositories.resumes import ResumeRepository
from app.repositories.task_adjustments import TaskAdjustmentRepository
from app.schemas.interviews import (
    InterviewComparison,
    InterviewMemoryCandidateRequest,
    InterviewMemoryCandidateResponse,
    InterviewReport,
    InterviewRetestRequest,
    InterviewRunReference,
    InterviewRunResponse,
    TrainingAction,
    TrainingActionPreviewItem,
    TrainingActionsConfirmResponse,
    TrainingActionsPreviewResponse,
    TrainingActionsRequest,
)


class InterviewCoachingService:
    def __init__(
        self, session: AsyncSession, settings: Settings, executor: AgentRunExecutor
    ) -> None:
        self._session = session
        self._settings = settings
        self._executor = executor
        self._interviews = InterviewRepository(session)
        self._memories = MemoryRepository(session)
        self._plans = PlanRepository(session)
        self._profiles = ProfileRepository(session)
        self._materials = ResumeRepository(session)
        self._adjustments = TaskAdjustmentRepository(session)
        self._runs = AgentRunRepository(session)

    async def create_memory_candidates(
        self,
        *,
        interview_id: UUID,
        user_id: UUID,
        payload: InterviewMemoryCandidateRequest,
    ) -> InterviewMemoryCandidateResponse:
        async with session_transaction(self._session):
            interview, report = await self._ready_report(interview_id, user_id)
            requested = set(payload.weakness_keys)
            known = {item.weakness_key for item in report.weaknesses}
            if not requested.issubset(known):
                raise self._validation("selected weakness does not exist in this report")
            created: list[UUID] = []
            eligible: list[str] = []
            skipped: list[str] = []
            now = datetime.now(UTC)
            for weakness in report.weaknesses:
                existing_memory = await self._memories.get_active_weakness(
                    user_id, weakness.weakness_key
                )
                is_eligible = (
                    len(set(weakness.evidence_turn_ids)) >= 2
                    or existing_memory is not None
                    or weakness.weakness_key in requested
                )
                if not is_eligible:
                    skipped.append(weakness.weakness_key)
                    continue
                eligible.append(weakness.weakness_key)
                existing_candidate = await self._memories.get_pending_weakness_candidate(
                    user_id, weakness.weakness_key
                )
                if existing_candidate is not None:
                    created.append(existing_candidate.id)
                    continue
                old_content = existing_memory.content_json if existing_memory is not None else {}
                old_sessions = old_content.get("evidence_session_ids", [])
                old_turns = old_content.get("evidence_turn_ids", [])
                session_ids = list(
                    dict.fromkeys(
                        [
                            *(old_sessions if isinstance(old_sessions, list) else []),
                            str(interview.id),
                        ]
                    )
                )
                turn_ids = list(
                    dict.fromkeys(
                        [
                            *(old_turns if isinstance(old_turns, list) else []),
                            *(str(item) for item in weakness.evidence_turn_ids),
                        ]
                    )
                )
                candidate = await self._memories.create_candidate(
                    user_id=user_id,
                    memory_type="execution_pattern",
                    summary=f"面试训练需持续关注：{weakness.topic}",
                    content_json={
                        "kind": "interview_weakness",
                        "weakness_key": weakness.weakness_key,
                        "claim": f"在{weakness.topic}方面需要持续训练",
                        "topic": weakness.topic,
                        "dimension": weakness.dimension,
                        "confidence": weakness.confidence,
                        "evidence_session_ids": session_ids,
                        "evidence_turn_ids": turn_ids,
                        "observation_count": len(turn_ids),
                        "first_observed_at": old_content.get("first_observed_at")
                        or now.isoformat(),
                        "last_observed_at": now.isoformat(),
                    },
                    sensitivity="sensitive",
                    proposed_by_run_id=interview.report_run_id,
                    expires_at=now + timedelta(days=14),
                )
                created.append(candidate.id)
            return InterviewMemoryCandidateResponse(
                created_candidate_ids=created,
                eligible_weakness_keys=eligible,
                skipped_weakness_keys=skipped,
            )

    async def preview_training_actions(
        self,
        *,
        interview_id: UUID,
        user_id: UUID,
        payload: TrainingActionsRequest,
    ) -> TrainingActionsPreviewResponse:
        async with session_transaction(self._session):
            _, report = await self._ready_report(interview_id, user_id)
            actions = self._selected_actions(report, payload)
            tasks = await self._adjustable_tasks(user_id)
            mode = "task_adjustment" if len(tasks) >= len(actions) else "replan"
            return TrainingActionsPreviewResponse(
                interview_id=interview_id,
                mode=mode,
                items=[
                    TrainingActionPreviewItem(
                        action_index=index,
                        action=action,
                        task_id=tasks[position].id if mode == "task_adjustment" else None,
                    )
                    for position, (index, action) in enumerate(actions)
                ],
            )

    async def confirm_training_actions(
        self,
        *,
        interview_id: UUID,
        user_id: UUID,
        payload: TrainingActionsRequest,
        idempotency_key: str,
    ) -> TrainingActionsConfirmResponse:
        request_fingerprint = self._hash(
            {
                "interview_id": str(interview_id),
                "payload": payload.model_dump(mode="json"),
            }
        )[:16]
        key_hash = sha256(idempotency_key.encode()).hexdigest()[:16]
        prefix = f"interview-training-{key_hash}-"
        created_run: AgentRun | None = None
        async with session_transaction(self._session):
            interview, report = await self._ready_report(interview_id, user_id)
            actions = self._selected_actions(report, payload)
            plan = await self._plans.get_current_cycle_for_user(user_id)
            if plan is not None:
                plan = await self._plans.get_for_user(plan.id, user_id, for_update=True)
            tasks = await self._adjustable_tasks(user_id, plan=plan, for_update=True)
            existing_run = await self._runs.get_by_idempotency(
                user_id, f"{prefix}replan"[:64]
            )
            existing_adjustments = await self._adjustments.list_by_prefix(user_id, prefix)
            if existing_run is not None or existing_adjustments:
                if existing_run is not None:
                    if (
                        existing_run.source_interview_report_session_id != interview.id
                        or request_fingerprint not in existing_run.request_text
                    ):
                        raise self._idempotency_error()
                    return self._replan_response(interview.id, existing_run)
                if any(request_fingerprint not in row.request_text for row in existing_adjustments):
                    raise self._idempotency_error()
                return TrainingActionsConfirmResponse(
                    interview_id=interview.id,
                    mode="task_adjustment",
                    adjustment_ids=[row.id for row in existing_adjustments],
                )

            if len(tasks) >= len(actions):
                profile = await self._profiles.get_for_user(user_id)
                if profile is None:
                    raise self._state("profile is required before adding training")
                adjustment_ids: list[UUID] = []
                now = datetime.now(UTC)
                for position, ((action_index, action), task) in enumerate(
                    zip(actions, tasks, strict=False)
                ):
                    if action.estimated_minutes > profile.time_budget_minutes:
                        raise self._validation("training action exceeds the daily time budget")
                    original = self._task_snapshot(task)
                    patch = {
                        "title": action.title,
                        "starter_action": action.starter_action,
                        "deliverable": action.deliverable,
                        "rationale": "来源于用户确认的面试报告训练建议。",
                        "estimated_minutes": action.estimated_minutes,
                    }
                    proposal = await self._adjustments.create(
                        {
                            "user_id": user_id,
                            "plan_id": task.plan_id,
                            "task_id": task.id,
                            "status": "applied",
                            "request_text": (
                                f"Interview report {interview.id} action {action_index} "
                                f"fingerprint={request_fingerprint}"
                            ),
                            "original_task_json": original,
                            "proposed_patch_json": patch,
                            "rationale": "用户一次批量确认的面试训练动作。",
                            "generation_method": "rule",
                            "task_version": task.version,
                            "idempotency_key": f"{prefix}{position}"[:64],
                            "applied_at": now,
                        }
                    )
                    task.title = action.title
                    task.starter_action = action.starter_action
                    task.deliverable = action.deliverable
                    task.rationale = "来源于用户确认的面试报告训练建议。"
                    task.estimated_minutes = action.estimated_minutes
                    task.completed_step_indexes_json = []
                    task.deliverable_verified = False
                    task.verification_status = "not_ready"
                    task.version += 1
                    task.updated_at = now
                    adjustment_ids.append(proposal.id)
                if plan is not None:
                    plan.version += 1
                    plan.updated_at = now
                await self._session.flush()
                return TrainingActionsConfirmResponse(
                    interview_id=interview.id,
                    mode="task_adjustment",
                    adjustment_ids=adjustment_ids,
                )

            if await self._runs.get_active_for_user(user_id) is not None:
                raise self._state("another Agent Run is already active")
            source_plan = await self._plans.get_current_cycle_for_user(user_id)
            if source_plan is None:
                source_plan = await self._plans.get_latest_completed_for_user(user_id)
            if source_plan is None:
                raise self._state("a source Plan is required for report-driven replanning")
            config = SnapshotService.build_config(self._settings)
            action_text = "；".join(
                f"{action.title}（{action.deliverable}，{action.estimated_minutes}分钟）"
                for _, action in actions
            )
            created_run = await self._runs.create(
                AgentRun(
                    user_id=user_id,
                    run_kind="planning",
                    source_interview_report_session_id=interview.id,
                    source_plan_id=source_plan.id,
                    idempotency_key=f"{prefix}replan"[:64],
                    request_text=(
                        f"根据已确认的面试训练建议调整计划：{action_text}。"
                        f" fingerprint={request_fingerprint}"
                    )[:2000],
                    hint_intent="replan",
                    replan_mode="adjust",
                    status="pending",
                    graph_version=config.graph_version,
                    config_snapshot_json=config.model_dump(mode="json"),
                    deadline_at=datetime.now(UTC) + timedelta(seconds=config.deadline_seconds),
                )
            )
            await EventRecorder(self._session).record(
                created_run.id,
                "run.created",
                {
                    "status": "pending",
                    "graph_version": created_run.graph_version,
                    "source_interview_report_session_id": str(interview.id),
                },
            )
        if created_run is None:
            raise RuntimeError("training confirmation did not converge")
        self._executor.submit(created_run.id)
        return self._replan_response(interview_id, created_run)

    async def create_retest(
        self,
        *,
        interview_id: UUID,
        user_id: UUID,
        payload: InterviewRetestRequest,
        idempotency_key: str,
    ) -> InterviewRunResponse:
        request_hash = self._hash(payload.model_dump(mode="json"))
        created_run: AgentRun | None = None
        async with session_transaction(self._session):
            baseline, report = await self._ready_report(interview_id, user_id)
            known = {item.weakness_key for item in report.weaknesses}
            if not set(payload.weakness_keys).issubset(known):
                raise self._validation("retest weakness does not exist in the baseline report")
            existing = await self._interviews.get_by_key(user_id, idempotency_key)
            if existing is not None:
                if (
                    existing.request_hash != request_hash
                    or existing.comparison_session_id != baseline.id
                ):
                    raise self._idempotency_error()
                run = await self._runs.get_for_interview(
                    session_id=existing.id, user_id=user_id, run_kind="interview_start"
                )
                if run is None:
                    raise self._state("retest start Run is missing")
                return self._interview_run_response(existing.id, run)
            resume_id = payload.resume_version_id or baseline.resume_version_id
            target_id = payload.job_target_id or baseline.job_target_id
            if await self._materials.get_resume(resume_id, user_id) is None:
                raise self._not_found("resume version")
            if await self._materials.get_job_target(target_id, user_id) is None:
                raise self._not_found("job target")
            if await self._runs.get_active_for_user(user_id) is not None:
                raise self._state("another Agent Run is already active")
            retest = await self._interviews.create_session(
                InterviewSession(
                    user_id=user_id,
                    resume_version_id=resume_id,
                    job_target_id=target_id,
                    interview_type=baseline.interview_type,
                    question_limit=payload.question_limit,
                    followup_limit=payload.followup_limit,
                    comparison_session_id=baseline.id,
                    context_summary_json={"retest_weakness_keys": payload.weakness_keys},
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                )
            )
            config = SnapshotService.build_interview_config(self._settings)
            created_run = await self._runs.create(
                AgentRun(
                    user_id=user_id,
                    run_kind="interview_start",
                    interview_session_id=retest.id,
                    source_interview_report_session_id=baseline.id,
                    idempotency_key=f"interview-retest-start-{retest.id}"[:64],
                    request_text="Generate a comparable retest question",
                    hint_intent="interview_start",
                    resolved_intent="interview_start",
                    status="pending",
                    graph_version=config.graph_version,
                    config_snapshot_json=config.model_dump(mode="json"),
                    deadline_at=datetime.now(UTC) + timedelta(seconds=config.deadline_seconds),
                )
            )
            await EventRecorder(self._session).record(
                created_run.id,
                "run.created",
                {
                    "status": "pending",
                    "graph_version": created_run.graph_version,
                    "run_kind": "interview_start",
                    "comparison_session_id": str(baseline.id),
                },
            )
        self._executor.submit(created_run.id)
        return self._interview_run_response(created_run.interview_session_id, created_run)

    async def get_comparison(
        self, *, interview_id: UUID, user_id: UUID
    ) -> InterviewComparison:
        async with session_transaction(self._session):
            interview, report = await self._ready_report(interview_id, user_id)
            if interview.comparison_session_id is None or report.comparison is None:
                raise self._state("this interview does not have a completed comparison")
            return report.comparison

    async def _ready_report(
        self, interview_id: UUID, user_id: UUID
    ) -> tuple[InterviewSession, InterviewReport]:
        interview = await self._interviews.get_session(interview_id, user_id)
        if interview is None:
            raise self._not_found("interview")
        if interview.report_status != "ready" or interview.report_json is None:
            raise self._state("the interview report is not ready")
        return interview, InterviewReport.model_validate(interview.report_json)

    @staticmethod
    def _selected_actions(
        report: InterviewReport, payload: TrainingActionsRequest
    ) -> list[tuple[int, TrainingAction]]:
        try:
            return [
                (index, report.recommended_training_actions[index])
                for index in payload.action_indexes
            ]
        except IndexError as exc:
            raise InterviewCoachingService._validation(
                "selected training action does not exist in this report"
            ) from exc

    async def _adjustable_tasks(
        self, user_id: UUID, *, plan: Plan | None = None, for_update: bool = False
    ) -> list[Task]:
        plan = plan or await self._plans.get_current_cycle_for_user(user_id)
        if plan is None or plan.status not in {"generated", "active"}:
            return []
        tasks = await self._plans.tasks_for_plan(plan.id, user_id)
        result = [task for task in tasks if task.state in {"pending", "in_progress"}]
        if for_update:
            locked: list[Task] = []
            for task in result:
                row = await self._plans.get_task_for_user(task.id, user_id, for_update=True)
                if row is not None:
                    locked.append(row)
            return locked
        return result

    @staticmethod
    def _task_snapshot(task: Task) -> dict[str, object]:
        return {
            "title": task.title,
            "starter_action": task.starter_action,
            "deliverable": task.deliverable,
            "rationale": task.rationale,
            "estimated_minutes": task.estimated_minutes,
            "version": task.version,
        }

    @staticmethod
    def _replan_response(interview_id: UUID, run: AgentRun) -> TrainingActionsConfirmResponse:
        return TrainingActionsConfirmResponse(
            interview_id=interview_id,
            mode="replan",
            run=InterviewRunReference(
                run_id=run.id,
                events_url=f"/api/v1/agent-runs/{run.id}/events",
            ),
        )

    @staticmethod
    def _interview_run_response(interview_id: UUID | None, run: AgentRun) -> InterviewRunResponse:
        if interview_id is None:
            raise RuntimeError("Interview Run is missing its Session")
        return InterviewRunResponse(
            interview_id=interview_id,
            run_id=run.id,
            events_url=f"/api/v1/agent-runs/{run.id}/events",
        )

    @staticmethod
    def _hash(payload: object) -> str:
        return sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @staticmethod
    def _not_found(name: str) -> AppError:
        return AppError(code="NOT_FOUND_RESOURCE", message=f"{name} was not found", status_code=404)

    @staticmethod
    def _state(message: str) -> AppError:
        return AppError(code="STATE_INTERVIEW_COACHING_INVALID", message=message, status_code=409)

    @staticmethod
    def _validation(message: str) -> AppError:
        return AppError(code="VALIDATION_INTERVIEW_COACHING", message=message, status_code=422)

    @staticmethod
    def _idempotency_error() -> AppError:
        return AppError(
            code="STATE_IDEMPOTENCY_KEY_REUSED",
            message="Idempotency-Key was already used with another request",
            status_code=HTTPStatus.CONFLICT,
        )
