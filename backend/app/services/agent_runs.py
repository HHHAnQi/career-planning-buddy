"""Agent Run creation, idempotency, cancellation, lookup, and SSE replay."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.executor import AgentRunExecutor
from app.core.config import Settings
from app.core.database import session_transaction
from app.core.exceptions import AppError
from app.harness.events import EventRecorder
from app.harness.snapshots import SnapshotService
from app.models.agent_run import AgentRun
from app.repositories.agent_runs import AgentRunRepository
from app.repositories.plans import PlanRepository
from app.schemas.agent_runs import (
    AgentRunCancelRequest,
    AgentRunResponse,
    ClarificationRequest,
    NavigationResult,
    PlanResultSummary,
    RunUserStatus,
    SafeResponse,
    TerminalResult,
)
from app.schemas.enums import ReplanMode, RunIntent, RunKind, RunResultKind, RunStatus
from app.schemas.interviews import InterviewReportResultSummary, InterviewTurnResultSummary
from app.schemas.resumes import ResumeAssessmentResultSummary

TERMINAL_STATUSES = {"completed", "degraded", "failed", "cancelled"}


class AgentRunService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        executor: AgentRunExecutor,
    ) -> None:
        self._session = session
        self._settings = settings
        self._executor = executor
        self._runs = AgentRunRepository(session)
        self._plans = PlanRepository(session)

    async def create(
        self,
        *,
        user_id: UUID,
        message: str,
        hint_intent: str | None,
        goal_type_override: str | None,
        source_plan_id: UUID | None,
        idempotency_key: str,
        goal_brief_id: UUID | None = None,
        schedule: bool = True,
    ) -> AgentRun:
        config = SnapshotService.build_config(self._settings)
        created = False
        try:
            async with session_transaction(self._session):
                existing = await self._runs.get_by_idempotency(user_id, idempotency_key)
                if existing is not None:
                    if (
                        existing.request_text != message
                        or existing.hint_intent != hint_intent
                        or existing.goal_type_override != goal_type_override
                        or existing.goal_brief_id != goal_brief_id
                        or (
                            source_plan_id is not None and existing.source_plan_id != source_plan_id
                        )
                    ):
                        raise AppError(
                            code="STATE_IDEMPOTENCY_KEY_REUSED",
                            message="Idempotency-Key was already used with another request",
                            status_code=HTTPStatus.CONFLICT,
                        )
                    return existing
                source_plan_id = await self._resolve_source_plan(
                    user_id=user_id,
                    hint_intent=hint_intent,
                    source_plan_id=source_plan_id,
                )
                active = await self._runs.get_active_for_user(user_id)
                if active is not None:
                    raise AppError(
                        code="STATE_RUN_ALREADY_ACTIVE",
                        message="another Agent Run is already active",
                        status_code=HTTPStatus.CONFLICT,
                    )
                run = AgentRun(
                    user_id=user_id,
                    goal_brief_id=goal_brief_id,
                    idempotency_key=idempotency_key,
                    request_text=message,
                    hint_intent=hint_intent,
                    goal_type_override=goal_type_override,
                    source_plan_id=source_plan_id,
                    status="pending",
                    graph_version=config.graph_version,
                    config_snapshot_json=config.model_dump(mode="json"),
                    deadline_at=datetime.now(UTC) + timedelta(seconds=config.deadline_seconds),
                )
                await self._runs.create(run)
                await EventRecorder(self._session).record(
                    run.id,
                    "run.created",
                    {"status": "pending", "graph_version": run.graph_version},
                )
                created = True
        except IntegrityError as exc:
            raise AppError(
                code="STATE_RUN_ALREADY_ACTIVE",
                message="another Agent Run is already active",
                status_code=HTTPStatus.CONFLICT,
            ) from exc
        if created and schedule:
            self._executor.submit(run.id)
        return run

    def schedule(self, run_id: UUID) -> None:
        """Wake the durable executor after the caller's transaction commits."""
        self._executor.submit(run_id)

    async def get_by_goal_brief(self, brief_id: UUID, user_id: UUID) -> AgentRun | None:
        return await self._runs.get_by_goal_brief(brief_id, user_id)

    async def get(self, run_id: UUID, user_id: UUID) -> AgentRun:
        async with session_transaction(self._session):
            run = await self._runs.get_for_user(run_id, user_id)
            if run is None:
                raise AppError(
                    code="NOT_FOUND_RUN",
                    message="Agent Run was not found",
                    status_code=HTTPStatus.NOT_FOUND,
                )
            return run

    async def get_response(self, run_id: UUID, user_id: UUID) -> AgentRunResponse:
        return self.to_response(await self.get(run_id, user_id))

    async def cancel(
        self,
        *,
        run_id: UUID,
        user_id: UUID,
        payload: AgentRunCancelRequest,
        idempotency_key: str,
    ) -> AgentRun:
        request_hash = self._request_fingerprint(payload.model_dump(mode="json"))
        async with session_transaction(self._session):
            run = await self._runs.get_for_user(run_id, user_id, for_update=True)
            if run is None:
                raise AppError(
                    code="NOT_FOUND_RUN",
                    message="Agent Run was not found",
                    status_code=HTTPStatus.NOT_FOUND,
                )
            if run.status == "cancelled":
                self._validate_cancel_idempotency(
                    run, idempotency_key=idempotency_key, request_hash=request_hash
                )
                return run
            if run.status in TERMINAL_STATUSES:
                raise AppError(
                    code="STATE_RUN_ALREADY_FINISHED",
                    message="Agent Run is already finished",
                    status_code=HTTPStatus.CONFLICT,
                )
            self._validate_cancel_idempotency(
                run, idempotency_key=idempotency_key, request_hash=request_hash
            )
            if run.cancel_requested_at is None:
                run.cancel_requested_at = datetime.now(UTC)
                run.cancel_idempotency_key = idempotency_key
                run.cancel_request_hash = request_hash
        await self._executor.request_cancel(run_id)
        return run

    @staticmethod
    def _request_fingerprint(payload: dict[str, object]) -> str:
        from hashlib import sha256

        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return sha256(encoded).hexdigest()

    @staticmethod
    def _validate_cancel_idempotency(
        run: AgentRun, *, idempotency_key: str, request_hash: str
    ) -> None:
        if (
            run.cancel_idempotency_key == idempotency_key
            and run.cancel_request_hash != request_hash
        ):
            raise AppError(
                code="STATE_IDEMPOTENCY_KEY_REUSED",
                message="Idempotency-Key was already used with another request",
                status_code=HTTPStatus.CONFLICT,
            )

    async def stream_events(
        self,
        *,
        run_id: UUID,
        user_id: UUID,
        after_sequence: int,
    ) -> AsyncIterator[str]:
        await self.get(run_id, user_id)
        sequence = after_sequence
        heartbeat_elapsed = 0.0
        poll = self._settings.agent_poll_interval_seconds
        while True:
            async with session_transaction(self._session):
                events = await self._runs.list_events_after(run_id, user_id, sequence)
                run = await self._runs.get_for_user(run_id, user_id)
            for event in events:
                sequence = event.sequence
                yield self._format_sse(
                    event.event_type,
                    event.payload_json,
                    event_id=event.sequence,
                )
            if run is None or (run.status in TERMINAL_STATUSES and not events):
                return
            if events:
                heartbeat_elapsed = 0
            else:
                await asyncio.sleep(poll)
                heartbeat_elapsed += poll
                if heartbeat_elapsed >= self._settings.agent_heartbeat_seconds:
                    heartbeat_elapsed = 0
                    yield self._format_sse(
                        "heartbeat",
                        {"run_id": str(run_id), "timestamp": datetime.now(UTC).isoformat()},
                    )

    async def _resolve_source_plan(
        self,
        *,
        user_id: UUID,
        hint_intent: str | None,
        source_plan_id: UUID | None,
    ) -> UUID | None:
        if source_plan_id is not None:
            source = await self._plans.get_for_user(source_plan_id, user_id)
            if source is None:
                raise AppError(
                    code="NOT_FOUND_SOURCE_PLAN",
                    message="source Plan was not found",
                    status_code=HTTPStatus.NOT_FOUND,
                )
            return source.id
        if hint_intent != "replan":
            return None
        source = await self._plans.get_active_for_user(user_id)
        if source is None:
            source = await self._plans.get_latest_completed_for_user(user_id)
        if source is None:
            raise AppError(
                code="VALIDATION_REPLAN_SOURCE_UNAVAILABLE",
                message="no Plan is available for replanning",
                status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            )
        return source.id

    @staticmethod
    def to_response(run: AgentRun) -> AgentRunResponse:
        result: TerminalResult | None = None
        if run.result_payload_json is not None:
            if run.result_kind == "plan":
                result = PlanResultSummary.model_validate(run.result_payload_json)
            elif run.result_kind == "clarification":
                result = ClarificationRequest.model_validate(run.result_payload_json)
            elif run.result_kind == "safe_response":
                result = SafeResponse.model_validate(run.result_payload_json)
            elif run.result_kind == "navigation":
                result = NavigationResult.model_validate(run.result_payload_json)
            elif run.result_kind == "interview_turn":
                result = InterviewTurnResultSummary.model_validate(run.result_payload_json)
            elif run.result_kind == "interview_report":
                result = InterviewReportResultSummary.model_validate(run.result_payload_json)
            elif run.result_kind in {"resume_assessment", "resume_optimization"}:
                result = ResumeAssessmentResultSummary.model_validate(run.result_payload_json)
        user_status, status_message = AgentRunService._user_status(run)
        return AgentRunResponse(
            run_id=run.id,
            run_kind=RunKind(run.run_kind),
            status=RunStatus(run.status),
            user_status=user_status,
            status_message=status_message,
            resolved_intent=(
                RunIntent(run.resolved_intent) if run.resolved_intent is not None else None
            ),
            replan_mode=(ReplanMode(run.replan_mode) if run.replan_mode is not None else None),
            result_kind=(RunResultKind(run.result_kind) if run.result_kind is not None else None),
            result=result,
            final_plan_id=run.final_plan_id,
            fallback_reason=run.fallback_reason,
            error_code=run.error_code,
            risk_category=run.risk_category,
            total_tokens_in=run.total_tokens_in,
            total_tokens_out=run.total_tokens_out,
            total_cost_cny=run.total_cost_cny,
            total_latency_ms=run.total_latency_ms,
            created_at=run.created_at,
            finished_at=run.finished_at,
            cancel_requested_at=run.cancel_requested_at,
        )

    @staticmethod
    def _user_status(run: AgentRun) -> tuple[RunUserStatus, str]:
        if run.cancel_requested_at is not None and run.status not in TERMINAL_STATUSES:
            return "stopping", "正在停止本次生成"
        if run.status == "pending":
            if run.attempt_count > 0:
                return "recovering", "服务恢复中，将自动继续"
            return "queued", "正在进入规划队列"
        if run.status == "running":
            return "generating", "正在整理适合你的行动路径"
        if run.status == "failed":
            return "failed", "生成失败，可以重新尝试"
        if run.status == "cancelled":
            return "cancelled", "本次生成已取消"
        if run.status == "degraded" and run.result_kind in {
            "clarification",
            "safe_response",
            "navigation",
        }:
            return "action_required", "需要你确认下一步"
        return "ready", "计划已经准备好"

    @staticmethod
    def _format_sse(
        event_type: str,
        data: dict[str, object],
        *,
        event_id: int | None = None,
    ) -> str:
        prefix = f"id: {event_id}\n" if event_id is not None else ""
        return (
            f"{prefix}event: {event_type}\n"
            f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"
        )
