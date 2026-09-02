"""Outcome snapshot builder.

After a Run reaches a terminal state, the TrialRunner reads the Run, its
terminal Plan + Tasks, persisted Steps, Events and ToolCalls from PostgreSQL
and freezes them into an immutable ``RunOutcome``. The persistent fields
(``run_id``, timestamps, latency, token counts) are NOT part of the
``transcript_hash`` -- only stable, semantically-meaningful fields enter the
projection so the hash is reproducible across reruns (revision #6).
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_run import AgentEvent, AgentRun, AgentStep, ToolCall
from app.models.plan import Plan, Task
from app.models.provider_call import ProviderCall
from app.repositories.plans import PlanRepository
from evals.v2.contracts import canonical_sha256

TERMINAL_EVENT_TYPES = frozenset(
    {"run.completed", "run.degraded", "run.failed", "run.cancelled"}
)


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """The persisted Runtime outcome for one Trial."""

    run_id: UUID
    user_id: UUID
    status: str
    result_kind: str | None
    final_plan_id: UUID | None
    error_code: str | None
    fallback_reason: str | None
    total_tokens_in: int
    total_tokens_out: int
    total_latency_ms: int
    plan: dict[str, object] | None
    tasks: list[dict[str, object]]
    steps: list[dict[str, object]]
    events: list[dict[str, object]]
    tool_calls: list[dict[str, object]]
    provider_calls: list[dict[str, object]]
    transcript_hash: str


def outcome_from_snapshot(
    snapshot: dict[str, object], *, transcript_hash: str
) -> RunOutcome:
    """Rebuild the immutable grader input persisted by ``TrialRunner``.

    Grading deliberately uses this representation instead of querying the
    mutable Run/Plan/Task tables again.  A malformed historical snapshot is
    an integrity failure, not a signal to fall back to live state.
    """

    run_raw = snapshot.get("run")
    if not isinstance(run_raw, dict):
        raise ValueError("outcome snapshot is missing the run block")

    def object_list(name: str) -> list[dict[str, object]]:
        raw = snapshot.get(name)
        if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
            raise ValueError(f"outcome snapshot {name} must be a list of objects")
        return [dict(item) for item in raw]

    plan_raw = snapshot.get("plan")
    if plan_raw is not None and not isinstance(plan_raw, dict):
        raise ValueError("outcome snapshot plan must be an object or null")
    final_plan_raw = run_raw.get("final_plan_id")
    try:
        return RunOutcome(
            run_id=UUID(str(run_raw["id"])),
            user_id=UUID(str(run_raw["user_id"])),
            status=str(run_raw["status"]),
            result_kind=(
                str(run_raw["result_kind"])
                if run_raw.get("result_kind") is not None
                else None
            ),
            final_plan_id=(
                UUID(str(final_plan_raw)) if final_plan_raw is not None else None
            ),
            error_code=(
                str(run_raw["error_code"])
                if run_raw.get("error_code") is not None
                else None
            ),
            fallback_reason=(
                str(run_raw["fallback_reason"])
                if run_raw.get("fallback_reason") is not None
                else None
            ),
            total_tokens_in=int(run_raw.get("total_tokens_in", 0)),
            total_tokens_out=int(run_raw.get("total_tokens_out", 0)),
            total_latency_ms=int(run_raw.get("total_latency_ms", 0)),
            plan=dict(plan_raw) if isinstance(plan_raw, dict) else None,
            tasks=object_list("tasks"),
            steps=object_list("steps"),
            events=object_list("events"),
            tool_calls=object_list("tool_calls"),
            provider_calls=object_list("provider_calls"),
            transcript_hash=transcript_hash,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("outcome snapshot contains invalid run fields") from exc


async def collect_outcome(
    session: AsyncSession,
    run: AgentRun,
    *,
    user_id: UUID,
) -> RunOutcome:
    """Freeze the post-terminal Run, Plan/Tasks, Steps, Events and ToolCalls."""

    steps = await session.scalars(
        select(AgentStep)
        .where(AgentStep.run_id == run.id)
        .order_by(AgentStep.sequence)
    )
    step_rows = list(steps)
    events = await session.scalars(
        select(AgentEvent)
        .where(AgentEvent.run_id == run.id)
        .order_by(AgentEvent.sequence)
    )
    event_rows = list(events)
    tool_rows = await session.scalars(
        select(ToolCall)
        .where(ToolCall.run_id == run.id)
        .order_by(ToolCall.round, ToolCall.created_at)
    )
    tool_call_rows = list(tool_rows)

    # PR-5: pull provider-call audit rows. They may be absent on prod live
    # Runs (no recorder is installed there); treat that as an empty projection.
    provider_call_rows_raw = await session.scalars(
        select(ProviderCall)
        .where(ProviderCall.run_id == run.id)
        .order_by(ProviderCall.sequence)
    )
    provider_call_rows = list(provider_call_rows_raw)
    provider_calls_proj: list[dict[str, object]] = [
        {
            "sequence": c.sequence,
            "provider_kind": c.provider_kind,
            "provider_method": c.provider_method,
            "logical_call_index": c.logical_call_index,
            "retry_attempt": c.retry_attempt,
            "status": c.status,
            "error_code": c.error_code,
            "tokens_in": c.tokens_in,
            "tokens_out": c.tokens_out,
            "latency_ms": c.latency_ms,
            "model_id": c.model_id,
            "request_projection_hash": c.request_projection_hash,
            "response_projection_hash": c.response_projection_hash,
        }
        for c in provider_call_rows
    ]

    plan_dict: dict[str, object] | None = None
    tasks_list: list[dict[str, object]] = []
    if run.final_plan_id is not None:
        plan = await session.get(Plan, run.final_plan_id)
        if plan is not None and plan.user_id == user_id:
            plan_dict = _plan_projection(plan)
            tasks_list = [
                _task_projection(task)
                for task in await PlanRepository(session).tasks_for_plan(
                    plan.id, user_id
                )
            ]
    return RunOutcome(
        run_id=run.id,
        user_id=run.user_id,
        status=run.status,
        result_kind=run.result_kind,
        final_plan_id=run.final_plan_id,
        error_code=run.error_code,
        fallback_reason=run.fallback_reason,
        total_tokens_in=run.total_tokens_in,
        total_tokens_out=run.total_tokens_out,
        total_latency_ms=run.total_latency_ms,
        plan=plan_dict,
        tasks=tasks_list,
        steps=[_step_projection(step) for step in step_rows],
        events=[_event_projection(event) for event in event_rows],
        tool_calls=[_tool_call_projection(tc) for tc in tool_call_rows],
        provider_calls=provider_calls_proj,
        transcript_hash=_compute_transcript_hash(
            run=run,
            steps=step_rows,
            events=event_rows,
            tool_calls=tool_call_rows,
        ),
    )


def terminal_events(outcome: RunOutcome) -> list[dict[str, object]]:
    return [event for event in outcome.events if event["event_type"] in TERMINAL_EVENT_TYPES]


def terminal_event_count(outcome: RunOutcome) -> int:
    return len(terminal_events(outcome))


def _plan_projection(plan: Plan) -> dict[str, object]:
    return {
        "id": str(plan.id),
        "status": plan.status,
        "summary": plan.summary,
        "rationale": plan.rationale,
        "horizon_start": plan.horizon_start.isoformat(),
        "horizon_end": plan.horizon_end.isoformat(),
        "evidence_refs_count": len(plan.evidence_refs_json),
    }


def _task_projection(task: Task) -> dict[str, object]:
    return {
        "id": str(task.id),
        "title": task.title,
        "task_type": task.task_type,
        "state": task.state,
        "starter_action": task.starter_action,
        "deliverable": task.deliverable,
        "estimated_minutes": task.estimated_minutes,
        "scheduled_date": task.scheduled_date.isoformat(),
    }


def _step_projection(step: AgentStep) -> dict[str, object]:
    # Stability-critical fields only (revision #6).
    return {
        "node": step.node_name,
        "status": step.status,
        "attempt": step.attempt,
        "error_code": step.error_code,
    }


def _event_projection(event: AgentEvent) -> dict[str, object]:
    payload = event.payload_json or {}
    return {
        "sequence": event.sequence,
        "event_type": event.event_type,
        "result_kind": payload.get("result_kind"),
        "error_code": payload.get("error_code"),
        "fallback_reason": payload.get("fallback_reason"),
        "tool_name": payload.get("tool_name"),
        "success": payload.get("success"),
    }


def _tool_call_projection(tool_call: ToolCall) -> dict[str, object]:
    # PCA-1 hotfix: project an ``id`` so callers needing a per-record key
    # (e.g. ``collect_evidence`` source_id) get a stable, unique identifier.
    # The historical primary key is the ORM id; we synthesize a fallback
    # from round+name when the row was assembled without an ORM id (defensive
    # -- the ToolCall model always populates ``id`` from gen_random_uuid()).
    fallback_id = f"r{tool_call.round}:{tool_call.tool_name}"
    return {
        "id": str(tool_call.id) if tool_call.id is not None else fallback_id,
        "tool_name": tool_call.tool_name,
        "round": tool_call.round,
        "success": tool_call.success,
        "error_code": tool_call.error_code,
        "result_hash": tool_call.result_hash,
    }


def _compute_transcript_hash(
    *,
    run: AgentRun,
    steps: list[AgentStep],
    events: list[AgentEvent],
    tool_calls: list[ToolCall],
) -> str:
    """Hash only stable, semantically-meaningful transcript fields.

    Excluded by design: ``run.id``, ``user_id``, ToolCall UUIDs, all timestamps
    (``created_at``/``started_at``/``finished_at``), ``total_latency_ms``,
    token counts, latency per step, ``result_hash`` content UUIDs, args
    payloads. What remains is the *shape* of what happened: node graph branch,
    status, repair attempt count, terminal kind/error, tool success/error.
    """

    projection = {
        "run": {
            "status": run.status,
            "result_kind": run.result_kind,
            "error_code": run.error_code,
            "fallback_reason": run.fallback_reason,
        },
        "steps": [
            {
                "node": step.node_name,
                "status": step.status,
                "attempt": step.attempt,
                "error_code": step.error_code,
            }
            for step in steps
        ],
        "events": [
            _event_projection(event) for event in events
        ],
        "tool_calls": [
            {
                "ordinal": ordinal,
                "tool_name": tc.tool_name,
                "round": tc.round,
                "success": tc.success,
                "error_code": tc.error_code,
            }
            for ordinal, tc in enumerate(tool_calls)
        ],
    }
    return canonical_sha256(projection)
