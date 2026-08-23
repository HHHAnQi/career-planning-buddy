"""Developer Trace inspection and isolated offline Replay use cases."""

import math
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from http import HTTPStatus
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.executor import AgentRunExecutor
from app.core.config import Settings
from app.core.database import session_transaction
from app.core.exceptions import AppError
from app.harness.redaction import redact, redacted_snapshot
from app.harness.runtime_bundle import (
    build_resume_runtime_bundle,
    get_or_create_runtime_bundle,
)
from app.harness.snapshots import SnapshotService
from app.models.agent_run import (
    AgentCheckpoint,
    AgentEvent,
    AgentRun,
    AgentStep,
    ReplayComparison,
    ToolCall,
)
from app.models.resume import ResumeAssessment
from app.repositories.dev import DevTraceRepository
from app.schemas.dev import (
    DevEventTrace,
    DevRunDetail,
    DevRunListResponse,
    DevRunSummary,
    DevSnapshot,
    DevStepTrace,
    DevToolTrace,
    FallbackReasonCount,
    RepairReportResponse,
    RepairStat,
    ReplayDiff,
    ReplayResponse,
    TerminalInvariant,
    UsageDailyPoint,
    UsageGraphBreakdown,
    UsageProviderKind,
    UsageReportResponse,
    UsageTotals,
)

TERMINAL_EVENTS = {"run.completed", "run.degraded", "run.failed", "run.cancelled"}


def _percentile(sorted_values: list[int], fraction: float) -> int:
    """Nearest-rank percentile over an already-sorted list; 0 when empty."""

    if not sorted_values:
        return 0
    rank = max(1, math.ceil(fraction * len(sorted_values)))
    return sorted_values[rank - 1]


class DevTraceService:
    def __init__(
        self,
        session: AsyncSession,
        executor: AgentRunExecutor | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._executor = executor
        self._settings = settings
        self._repo = DevTraceRepository(session)

    async def list_runs(
        self,
        *,
        status: str | None,
        result_kind: str | None,
        error_code: str | None,
        cursor: UUID | None,
        limit: int,
    ) -> DevRunListResponse:
        async with session_transaction(self._session):
            rows = await self._repo.list_runs(
                status=status,
                result_kind=result_kind,
                error_code=error_code,
                cursor=cursor,
                limit=limit,
            )
        has_more = len(rows) > limit
        rows = rows[:limit]
        return DevRunListResponse(
            items=[self._summary(row) for row in rows],
            next_cursor=rows[-1].id if has_more and rows else None,
        )

    async def usage_report(self, *, days: int) -> UsageReportResponse:
        """Aggregate cost, latency, and provider health over the window."""

        since = datetime.now(UTC) - timedelta(days=days)
        async with session_transaction(self._session):
            runs = await self._repo.usage_runs(since=since)
            provider_rows = await self._repo.usage_provider_calls(since=since)

        terminal_runs = [run for run in runs if run.status in {"completed", "degraded", "failed"}]
        latencies = sorted(
            run.total_latency_ms for run in terminal_runs if run.total_latency_ms > 0
        )
        total_cost = sum((run.total_cost_cny for run in runs), Decimal("0"))

        graphs: dict[tuple[str, str | None], list[AgentRun]] = {}
        daily: dict[date, list[AgentRun]] = {}
        for run in runs:
            graphs.setdefault((run.graph_version, run.model_id), []).append(run)
            daily.setdefault(run.created_at.astimezone(UTC).date(), []).append(run)

        provider_kinds: dict[str, dict[str, int]] = {}
        for kind, call_status, count, latency_total in provider_rows:
            bucket = provider_kinds.setdefault(
                kind, {"call_count": 0, "error_count": 0, "latency_total": 0}
            )
            bucket["call_count"] += count
            if call_status == "error":
                bucket["error_count"] += count
            bucket["latency_total"] += latency_total

        return UsageReportResponse(
            window_days=days,
            generated_at=datetime.now(UTC),
            totals=UsageTotals(
                run_count=len(runs),
                completed_count=sum(1 for run in runs if run.status == "completed"),
                degraded_count=sum(1 for run in runs if run.status == "degraded"),
                failed_count=sum(1 for run in runs if run.status == "failed"),
                fallback_count=sum(1 for run in runs if run.fallback_reason is not None),
                total_cost_cny=total_cost,
                total_tokens_in=sum(run.total_tokens_in for run in runs),
                total_tokens_out=sum(run.total_tokens_out for run in runs),
                avg_cost_per_run_cny=(
                    (total_cost / len(runs)).quantize(Decimal("0.000001")) if runs else Decimal("0")
                ),
                latency_p50_ms=_percentile(latencies, 0.50),
                latency_p95_ms=_percentile(latencies, 0.95),
                latency_max_ms=latencies[-1] if latencies else 0,
            ),
            graphs=[
                UsageGraphBreakdown(
                    graph_version=graph_version,
                    model_id=model_id,
                    run_count=len(group),
                    total_cost_cny=sum((run.total_cost_cny for run in group), Decimal("0")),
                    avg_latency_ms=(
                        sum(run.total_latency_ms for run in group) // len(group) if group else 0
                    ),
                )
                for (graph_version, model_id), group in sorted(
                    graphs.items(), key=lambda item: (item[0][0], item[0][1] or "")
                )
            ],
            daily=[
                UsageDailyPoint(
                    date=day,
                    run_count=len(group),
                    total_cost_cny=sum((run.total_cost_cny for run in group), Decimal("0")),
                )
                for day, group in sorted(daily.items())
            ],
            provider_kinds=[
                UsageProviderKind(
                    provider_kind=kind,
                    call_count=bucket["call_count"],
                    error_count=bucket["error_count"],
                    avg_latency_ms=(
                        bucket["latency_total"] // bucket["call_count"]
                        if bucket["call_count"]
                        else 0
                    ),
                )
                for kind, bucket in sorted(provider_kinds.items())
            ],
        )

    async def repair_report(self, *, days: int) -> RepairReportResponse:
        """Repair-mechanism outcomes: trigger, success, and fallback reasons.

        Repairs are identified by their persisted step prompt_version
        (``format_repair`` / ``business_repair`` substrings cover both the
        real and mock version strings), and failures by the run's terminal
        fallback_reason.
        """

        since = datetime.now(UTC) - timedelta(days=days)
        async with session_transaction(self._session):
            version_counts = await self._repo.repair_prompt_version_counts(since=since)
            reason_counts = await self._repo.fallback_reason_counts(since=since)

        reasons = {reason: count for reason, count in reason_counts}

        def _stat(
            *,
            kind: str,
            version_substring: str,
            failed_reasons: set[str],
            declined_reasons: set[str],
        ) -> RepairStat:
            triggered = sum(
                count
                for version, count in version_counts
                if version_substring in version
            )
            failed_after_attempt = sum(
                count for reason, count in reason_counts if reason in failed_reasons
            )
            declined = sum(
                count for reason, count in reason_counts if reason in declined_reasons
            )
            succeeded = max(triggered - failed_after_attempt, 0)
            return RepairStat(
                kind=kind,
                triggered=triggered,
                succeeded=succeeded,
                failed_after_attempt=failed_after_attempt,
                declined_by_budget=declined,
                success_rate=succeeded / triggered if triggered else 0.0,
            )

        return RepairReportResponse(
            window_days=days,
            generated_at=datetime.now(UTC),
            repairs=[
                _stat(
                    kind="format_repair",
                    version_substring="format_repair",
                    failed_reasons={"format_repair_failed"},
                    declined_reasons=set(),
                ),
                _stat(
                    kind="business_repair",
                    version_substring="business_repair",
                    failed_reasons={"business_repair_invalid"},
                    declined_reasons={
                        "business_repair_budget_insufficient",
                        "business_repair_exhausted",
                    },
                ),
            ],
            fallback_reasons=[
                FallbackReasonCount(reason=reason, count=count)
                for reason, count in sorted(reasons.items())
            ],
        )

    async def get_run(self, run_id: UUID) -> DevRunDetail:
        async with session_transaction(self._session):
            run = await self._repo.get_run(run_id)
            if run is None:
                raise self._not_found()
            steps, tools, events = await self._repo.get_trace(run_id)
        input_snapshot = None
        if run.input_snapshot_json is not None:
            data, digest = redacted_snapshot(run.input_snapshot_json)
            input_snapshot = DevSnapshot(data=data, sha256=digest)
        config_data, config_digest = redacted_snapshot(run.config_snapshot_json)
        terminal_count = sum(event.event_type in TERMINAL_EVENTS for event in events)
        terminal_is_last = bool(events) and events[-1].event_type in TERMINAL_EVENTS
        return DevRunDetail(
            run=self._summary(run),
            request_text=run.request_text,
            input_snapshot=input_snapshot,
            config_snapshot=DevSnapshot(data=config_data, sha256=config_digest),
            result=redact(run.result_payload_json),
            steps=[
                DevStepTrace(
                    sequence=row.sequence,
                    node_name=row.node_name,
                    attempt=row.attempt,
                    status=row.status,
                    prompt_version=row.prompt_version,
                    model_id=row.model_id,
                    tokens_in=row.tokens_in,
                    tokens_out=row.tokens_out,
                    latency_ms=row.latency_ms,
                    input_hash=row.input_hash,
                    output_hash=row.output_hash,
                    trace_data=redact(row.trace_data),
                    error_code=row.error_code,
                )
                for row in steps
            ],
            tools=[
                DevToolTrace(
                    tool_call_id=row.id,
                    step_id=row.step_id,
                    tool_name=row.tool_name,
                    contract_version=row.tool_contract_version,
                    round=row.round,
                    args=redact(row.args_json),
                    args_hash=row.args_hash,
                    result=redact(row.result_json),
                    result_hash=row.result_hash,
                    provider=row.provider,
                    latency_ms=row.latency_ms,
                    success=row.success,
                    error_code=row.error_code,
                )
                for row in tools
            ],
            events=[
                DevEventTrace(
                    sequence=row.sequence,
                    event_type=row.event_type,
                    payload=redact(row.payload_json),
                    created_at=row.created_at,
                )
                for row in events
            ],
            terminal_invariant=TerminalInvariant(
                terminal_count=terminal_count,
                terminal_is_last=terminal_is_last,
                valid=terminal_count == 1 and terminal_is_last,
            ),
        )

    async def legacy_trace_clone(self, run_id: UUID, *, tool_mode: str) -> ReplayResponse:
        """Clone captured trace rows without executing the Agent graph.

        This compatibility operation is intentionally not Replay: it does not invoke a
        Provider, rebuild context, execute nodes, or compare a newly generated result.
        """
        async with session_transaction(self._session):
            source = await self._repo.get_run(run_id)
            if source is None:
                raise self._not_found()
            if source.status not in {"completed", "degraded", "failed", "cancelled"}:
                raise AppError(
                    code="REPLAY_SOURCE_NOT_TERMINAL",
                    message="only terminal Runs can be replayed",
                    status_code=HTTPStatus.CONFLICT,
                )
            source_steps, source_tools, _source_events = await self._repo.get_trace(run_id)
            if tool_mode == "fixture" and any(row.result_json is None for row in source_tools):
                raise AppError(
                    code="REPLAY_FIXTURE_MISSING",
                    message="a captured Tool result required for Replay is missing",
                    status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                )

            now = datetime.now(UTC)
            config = dict(source.config_snapshot_json)
            config.update(
                {
                    "provider": "mock",
                    "model_alias": "mock-career-planner-v1",
                    "replay_tool_mode": tool_mode,
                    "replay_deterministic": tool_mode == "fixture",
                    "execution_kind": "legacy_trace_clone",
                }
            )
            replay = AgentRun(
                user_id=source.user_id,
                idempotency_key=f"replay-{uuid4().hex}",
                request_text=source.request_text,
                hint_intent=source.hint_intent,
                resolved_intent=source.resolved_intent,
                replan_mode=source.replan_mode,
                requested_horizon_weeks=source.requested_horizon_weeks,
                goal_type_override=source.goal_type_override,
                source_plan_id=source.source_plan_id,
                source_review_id=source.source_review_id,
                replay_of_run_id=source.id,
                status=source.status,
                result_kind=source.result_kind,
                result_payload_json=source.result_payload_json,
                final_plan_id=source.final_plan_id,
                graph_version=source.graph_version,
                input_snapshot_json=source.input_snapshot_json,
                config_snapshot_json=config,
                model_id="mock-career-planner-v1",
                total_tokens_in=source.total_tokens_in,
                total_tokens_out=source.total_tokens_out,
                total_cost_cny=Decimal("0"),
                total_latency_ms=0,
                fallback_reason=source.fallback_reason,
                error_code=source.error_code,
                error_message=source.error_message,
                risk_category=source.risk_category,
                deadline_at=now,
                created_at=now,
                started_at=now,
                finished_at=now,
            )
            self._session.add(replay)
            await self._session.flush()

            step_ids: dict[UUID, UUID] = {}
            for source_step in source_steps:
                cloned = AgentStep(
                    run_id=replay.id,
                    sequence=source_step.sequence,
                    node_name=source_step.node_name,
                    attempt=source_step.attempt,
                    status=source_step.status,
                    prompt_version=source_step.prompt_version,
                    model_id=("mock-career-planner-v1" if source_step.model_id else None),
                    tokens_in=source_step.tokens_in,
                    tokens_out=source_step.tokens_out,
                    cost_cny=Decimal("0"),
                    latency_ms=0,
                    input_hash=source_step.input_hash,
                    output_hash=source_step.output_hash,
                    trace_data={
                        **source_step.trace_data,
                        "execution_kind": "legacy_trace_clone",
                    },
                    error_code=source_step.error_code,
                    error_message=source_step.error_message,
                    created_at=now,
                    finished_at=now,
                )
                self._session.add(cloned)
                await self._session.flush()
                step_ids[source_step.id] = cloned.id
            for source_tool in source_tools:
                self._session.add(
                    ToolCall(
                        run_id=replay.id,
                        step_id=step_ids[source_tool.step_id],
                        tool_name=source_tool.tool_name,
                        tool_contract_version=source_tool.tool_contract_version,
                        round=source_tool.round,
                        args_json=source_tool.args_json,
                        args_hash=source_tool.args_hash,
                        result_json=source_tool.result_json,
                        result_preview=source_tool.result_preview,
                        result_hash=source_tool.result_hash,
                        provider="fixture" if tool_mode == "fixture" else source_tool.provider,
                        latency_ms=0,
                        success=source_tool.success,
                        error_code=source_tool.error_code,
                        created_at=now,
                    )
                )
            event_payloads: list[tuple[str, dict[str, object]]] = [
                ("run.created", {"status": "pending", "replay_of_run_id": str(source.id)}),
                (
                    "legacy_trace_clone.started",
                    {
                        "tool_mode": tool_mode,
                        "source_run_id": str(source.id),
                        "execution_kind": "legacy_trace_clone",
                    },
                ),
            ]
            event_payloads.extend(
                (
                    "node.completed",
                    {
                        "node_name": step.node_name,
                        "step_sequence": step.sequence,
                        "status": step.status,
                        "latency_ms": 0,
                        "execution_kind": "legacy_trace_clone",
                    },
                )
                for step in source_steps
            )
            terminal_type = f"run.{source.status}"
            terminal_payload: dict[str, object] = {
                "status": source.status,
                "execution_kind": "legacy_trace_clone",
            }
            if source.result_kind is not None:
                terminal_payload["result_kind"] = source.result_kind
            if source.final_plan_id is not None:
                terminal_payload["final_plan_id"] = str(source.final_plan_id)
            if source.error_code is not None:
                terminal_payload["error_code"] = source.error_code
            event_payloads.append((terminal_type, terminal_payload))
            for sequence, (event_type, payload) in enumerate(event_payloads, start=1):
                self._session.add(
                    AgentEvent(
                        run_id=replay.id,
                        sequence=sequence,
                        event_type=event_type,
                        payload_json=payload,
                        created_at=now,
                    )
                )
            replay.next_event_sequence = len(event_payloads) + 1
            replay.next_step_sequence = len(source_steps) + 1
            await self._session.flush()
            replay_id = replay.id
        return ReplayResponse(
            run_id=replay_id,
            replay_of_run_id=run_id,
            status=source.status,
            deterministic=tool_mode == "fixture",
            execution_kind="legacy_trace_clone",
        )

    async def replay_v2(
        self,
        run_id: UUID,
        *,
        mode: str,
        target_runtime_bundle_id: UUID | None,
    ) -> ReplayResponse:
        """Execute a new Resume graph from the frozen source snapshot."""
        if self._executor is None:
            raise RuntimeError("Replay V2 executor is not configured")
        async with session_transaction(self._session):
            source = await self._repo.get_run(run_id)
            if source is None:
                raise self._not_found()
            if source.status not in {"completed", "degraded", "failed", "cancelled"}:
                raise AppError(
                    code="REPLAY_SOURCE_NOT_TERMINAL",
                    message="only terminal Runs can be replayed",
                    status_code=HTTPStatus.CONFLICT,
                )
            if source.run_kind != "resume_optimization":
                raise AppError(
                    code="REPLAY_KIND_UNSUPPORTED",
                    message="Replay V2 currently requires a Resume optimization Run",
                    status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                )
            if source.input_snapshot_json is None:
                raise AppError(
                    code="REPLAY_SNAPSHOT_MISSING",
                    message="the frozen input snapshot is missing",
                    status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                )
            if mode == "exact_fixture_replay" and target_runtime_bundle_id not in {
                None,
                source.runtime_bundle_id,
            }:
                raise AppError(
                    code="REPLAY_RUNTIME_BUNDLE_MISMATCH",
                    message="exact replay must use the source Runtime Bundle",
                    status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                )
            if source.runtime_bundle_id is None:
                raise AppError(
                    code="REPLAY_RUNTIME_BUNDLE_MISSING",
                    message="the immutable Runtime Bundle is missing",
                    status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                )
            _steps, source_tools, _events = await self._repo.get_trace(run_id)
            provider_fixture = await self._session.scalar(
                select(AgentCheckpoint).where(
                    AgentCheckpoint.run_id == run_id,
                    AgentCheckpoint.node_name == "resume_provider_generate",
                )
            )
            if (
                not source_tools
                or any(row.result_json is None or not row.success for row in source_tools)
                or (mode == "exact_fixture_replay" and provider_fixture is None)
            ):
                raise AppError(
                    code="REPLAY_FIXTURE_MISSING",
                    message=(
                        "complete Tool fixtures are required; Exact Replay also requires "
                        "a Provider fixture"
                    ),
                    status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                )
            runtime_bundle_id = source.runtime_bundle_id
            if mode == "candidate_comparison":
                if self._settings is None:
                    raise RuntimeError("candidate comparison requires Runtime settings")
                current_config = SnapshotService.build_resume_optimization_config(self._settings)
                current_bundle = await get_or_create_runtime_bundle(
                    self._session,
                    build_resume_runtime_bundle(self._settings, current_config),
                )
                if target_runtime_bundle_id not in {None, current_bundle.id}:
                    raise AppError(
                        code="REPLAY_RUNTIME_BUNDLE_NOT_ACTIVE",
                        message=(
                            "candidate comparison target must be the active server Runtime Bundle"
                        ),
                        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                    )
                runtime_bundle_id = current_bundle.id
                config = current_config.model_dump(mode="json")
                config.update(
                    {
                        "execution_kind": "candidate_comparison",
                        "replay_tool_mode": "fixture",
                    }
                )
            else:
                config = dict(source.config_snapshot_json)
                config.update(
                    {
                        "execution_kind": "exact_fixture_replay",
                        "replay_tool_mode": "fixture",
                    }
                )
            deadline_seconds = config.get("deadline_seconds", 45)
            if not isinstance(deadline_seconds, int | float):
                deadline_seconds = 45
            replay = AgentRun(
                user_id=source.user_id,
                run_kind=source.run_kind,
                interview_session_id=source.interview_session_id,
                idempotency_key=f"replay-v2-{uuid4().hex}",
                request_text=source.request_text,
                hint_intent=source.hint_intent,
                resolved_intent=source.resolved_intent,
                replay_of_run_id=source.id,
                runtime_bundle_id=runtime_bundle_id,
                resume_version_id=source.resume_version_id,
                job_target_id=source.job_target_id,
                status="pending",
                graph_version=source.graph_version,
                input_snapshot_json=source.input_snapshot_json,
                config_snapshot_json=config,
                deadline_at=datetime.now(UTC) + timedelta(seconds=int(deadline_seconds)),
            )
            self._session.add(replay)
            await self._session.flush()
            self._session.add(
                AgentEvent(
                    run_id=replay.id,
                    sequence=1,
                    event_type="run.created",
                    payload_json={
                        "status": "pending",
                        "replay_of_run_id": str(source.id),
                        "execution_kind": mode,
                        "tool_mode": "fixture",
                    },
                )
            )
            replay.next_event_sequence = 2
            replay_id = replay.id
        self._executor.submit(replay_id)
        return ReplayResponse(
            run_id=replay_id,
            replay_of_run_id=run_id,
            status="pending",
            deterministic=mode == "exact_fixture_replay",
            execution_kind=mode,
        )

    async def replay_diff(self, replay_run_id: UUID) -> ReplayDiff:
        async with session_transaction(self._session):
            replay = await self._repo.get_run(replay_run_id)
            if replay is None or replay.replay_of_run_id is None:
                raise self._not_found()
            source = await self._repo.get_run(replay.replay_of_run_id)
            if source is None:
                raise self._not_found()
            if replay.status not in {"completed", "degraded", "failed", "cancelled"}:
                raise AppError(
                    code="REPLAY_NOT_TERMINAL",
                    message="Replay diff is available after the Replay reaches a terminal state",
                    status_code=HTTPStatus.CONFLICT,
                )
            source_assessment = await self._session.scalar(
                select(ResumeAssessment).where(ResumeAssessment.source_run_id == source.id)
            )
            replay_assessment = await self._session.scalar(
                select(ResumeAssessment).where(ResumeAssessment.source_run_id == replay.id)
            )
            _source_steps, source_tools, _ = await self._repo.get_trace(source.id)
            replay_steps, replay_tools, _ = await self._repo.get_trace(replay.id)
        source_result = self._digest(source.result_payload_json)
        replay_result = self._digest(replay.result_payload_json)
        source_context = source_assessment.context_manifest_json if source_assessment else None
        replay_context = replay_assessment.context_manifest_json if replay_assessment else None
        source_claims = (
            self._canonical_claims(source_assessment.findings_json) if source_assessment else None
        )
        replay_claims = (
            self._canonical_claims(replay_assessment.findings_json) if replay_assessment else None
        )
        source_tool_payload = [
            {"name": item.tool_name, "args": item.args_json, "result": item.result_json}
            for item in source_tools
        ]
        replay_tool_payload = [
            {"name": item.tool_name, "args": item.args_json, "result": item.result_json}
            for item in replay_tools
        ]
        source_validation = next(
            (
                item.trace_data
                for item in _source_steps
                if item.node_name == "resume_faithfulness_validator"
            ),
            None,
        )
        replay_validation = next(
            (
                item.trace_data
                for item in replay_steps
                if item.node_name == "resume_faithfulness_validator"
            ),
            None,
        )
        comparisons = {
            "status": source.status == replay.status,
            "result_kind": source.result_kind == replay.result_kind,
            "context": self._digest(source_context) == self._digest(replay_context),
            "tools": self._digest(source_tool_payload) == self._digest(replay_tool_payload),
            "claims": self._digest(source_claims) == self._digest(replay_claims),
            "validation": self._digest(source_validation) == self._digest(replay_validation),
        }
        changed = [name for name, equal in comparisons.items() if not equal]
        semantic_equal = all(comparisons.values())
        diff_payload = {
            "context": self._pair_hash(source_context, replay_context),
            "tools": self._pair_hash(source_tool_payload, replay_tool_payload),
            "claims": self._pair_hash(source_claims, replay_claims),
            "validation": self._pair_hash(source_validation, replay_validation),
            "usage": {
                "source": self._usage(source),
                "replay": self._usage(replay),
            },
            "changed_fields": changed,
        }
        async with session_transaction(self._session):
            existing = await self._session.scalar(
                select(ReplayComparison).where(ReplayComparison.replay_run_id == replay.id)
            )
            if existing is None:
                self._session.add(
                    ReplayComparison(
                        source_run_id=source.id,
                        replay_run_id=replay.id,
                        comparison_version="resume-semantic-diff-v2",
                        semantic_equal=semantic_equal,
                        diff_json=diff_payload,
                    )
                )
        return ReplayDiff(
            source_run_id=source.id,
            replay_run_id=replay.id,
            source_status=source.status,
            replay_status=replay.status,
            input_snapshot_equal=self._digest(source.input_snapshot_json)
            == self._digest(replay.input_snapshot_json),
            semantic_equal=semantic_equal,
            source_result_sha256=source_result,
            replay_result_sha256=replay_result,
            changed_fields=changed,
            context_diff=diff_payload["context"],
            tool_diff=diff_payload["tools"],
            claim_diff=diff_payload["claims"],
            validation_diff=diff_payload["validation"],
            usage_diff=diff_payload["usage"],
        )

    @classmethod
    def _pair_hash(cls, source: object, replay: object) -> dict[str, object]:
        source_hash, replay_hash = cls._digest(source), cls._digest(replay)
        return {
            "equal": source_hash == replay_hash,
            "source_sha256": source_hash,
            "replay_sha256": replay_hash,
        }

    @staticmethod
    def _usage(run: AgentRun) -> dict[str, object]:
        return {
            "tokens_in": run.total_tokens_in,
            "tokens_out": run.total_tokens_out,
            "cost_cny": str(run.total_cost_cny),
            "latency_ms": run.total_latency_ms,
        }

    @staticmethod
    def _canonical_claims(items: list[dict[str, object]]) -> list[dict[str, object]]:
        return [
            {key: value for key, value in item.items() if key != "consumed_tool_call_ids"}
            for item in items
        ]

    @staticmethod
    def _digest(value: object | None) -> str | None:
        if value is None:
            return None
        import json

        return sha256(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

    @staticmethod
    def _summary(run: AgentRun) -> DevRunSummary:
        user_ref = sha256(str(run.user_id).encode("utf-8")).hexdigest()[:12]
        return DevRunSummary(
            run_id=run.id,
            replay_of_run_id=run.replay_of_run_id,
            user_ref=user_ref,
            status=run.status,
            result_kind=run.result_kind,
            resolved_intent=run.resolved_intent,
            graph_version=run.graph_version,
            model_id=run.model_id,
            total_tokens_in=run.total_tokens_in,
            total_tokens_out=run.total_tokens_out,
            total_cost_cny=run.total_cost_cny,
            total_latency_ms=run.total_latency_ms,
            fallback_reason=run.fallback_reason,
            error_code=run.error_code,
            created_at=run.created_at,
            finished_at=run.finished_at,
        )

    @staticmethod
    def _not_found() -> AppError:
        return AppError(
            code="NOT_FOUND_RUN",
            message="Agent Run was not found",
            status_code=HTTPStatus.NOT_FOUND,
        )
