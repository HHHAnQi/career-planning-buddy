"""Evidence projection for one Trial.

``collect_evidence`` freezes a bounded set of ``EvidenceItem`` rows from a
completed Run's outcome projection, the versioned ``EvalCase``'s scenario /
expected_outcome / trajectory_policy, and a few side-channel signals the
Graders need (risk match, repair count, visible-evidence refs). It does NOT
copy raw Provider transcripts or arguments payloads -- those are explicitly
denied across all six Grader domains (see ``graders/registry.py``).

Content hashing uses ``canonical_sha256`` so a re-collect of the same
projection yields the same ``content_hash``; the DB UNIQUE over
``(trial_id, kind, source_type, source_id)`` therefore either leaves the
existing rows untouched or writes new rows whose ids differ from any prior
Score's ``evidence_item_ids`` -- this is what makes old scores non-silently
reusable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_run import AgentEvent, AgentStep
from app.models.evidence import Memory
from app.models.plan import Plan, Task
from app.services.input_safety import HIGH_RISK_PATTERNS
from evals.v2.collectors.outcome import (
    RunOutcome,
    _plan_projection,
    _task_projection,
)
from evals.v2.contracts import EvalCase, canonical_sha256
from evals.v2.graders.base import EvidenceItem, EvidenceKind

if TYPE_CHECKING:
    from app.models.agent_run import AgentRun


def _item(
    *, trial_id: UUID, kind: EvidenceKind, source_type: str, source_id: str,
    projection: dict[str, object], sensitivity: str = "normal",
) -> EvidenceItem:
    return EvidenceItem(
        id=uuid4(),
        trial_id=trial_id,
        kind=kind,
        source_type=source_type,
        source_id=source_id,
        content_hash=canonical_sha256(projection),
        projection=projection,
        sensitivity=sensitivity,
    )


def _risk_signals(message: str) -> dict[str, object]:
    matched = [rule_id for rule_id, pattern in HIGH_RISK_PATTERNS if pattern.search(message)]
    return {
        "level": "high" if matched else "none",
        "category": "self_harm" if matched else None,
        "matched_rule_ids": matched,
    }


def _repair_signal(
    events: list[AgentEvent], steps: list[AgentStep]
) -> dict[str, object]:
    """Count repair signals from event payloads + step attempts."""

    format_attempts = 0
    business_attempts = 0
    for event in events:
        etype = event.event_type
        if "format" in etype and "repair" in etype:
            format_attempts += 1
        elif "business" in etype and "repair" in etype:
            business_attempts += 1
    # Also infer from step retries: any step with attempt > 1 indicates a retry.
    step_retries = sum(1 for step in steps if step.attempt > 1)
    # Without granular event instrumentation we attribute all step retries to
    # the most generic "total" counter; the per-kind breakdown stays cautious.
    total = max(format_attempts + business_attempts, 1 if step_retries > 0 else 0)
    has_any_repair = bool(format_attempts or business_attempts or step_retries)
    return {
        "format_repair_attempts": format_attempts,
        "business_repair_attempts": business_attempts,
        "total_repair_attempts": total if has_any_repair else 0,
    }


def _find_companion_for(events: list[AgentEvent]) -> dict[str, object] | None:
    """Project the companion message event payload (no raw PII retained)."""

    for event in reversed(events):
        if event.event_type == "companion.message":
            payload = event.payload_json or {}
            text = str(payload.get("message", ""))
            return {"output": text[:1000]}  # truncated; Safety grader scans for fragments only
    return None


def _visible_refs_from_steps(steps: list[AgentStep]) -> dict[str, object]:
    """Read the final candidate-producing call's frozen visibility window."""

    candidate_nodes = {"career_planning_agent", "revise_or_fallback"}
    for step in reversed(steps):
        if step.node_name not in candidate_nodes or step.status != "completed":
            continue
        trace = step.trace_data or {}
        raw_refs = trace.get("visible_evidence_refs")
        if not isinstance(raw_refs, list):
            continue
        refs = [dict(ref) for ref in raw_refs if isinstance(ref, dict)]
        return {
            "visible_refs": refs,
            "call_id": trace.get("evidence_call_id"),
            "catalog_hash": trace.get("evidence_catalog_hash"),
            "source_step_id": str(step.id),
        }
    return {
        "visible_refs": [],
        "call_id": None,
        "catalog_hash": None,
        "source_step_id": None,
    }


async def collect_evidence(
    session: AsyncSession,
    *,
    trial_id: UUID,
    run: AgentRun,
    outcome: RunOutcome,
    case: EvalCase,
) -> list[EvidenceItem]:
    from evals.v2.contracts import PlanningEvalScenario

    if not isinstance(case.scenario, PlanningEvalScenario):
        raise TypeError("planning evidence collector requires a planning scenario")
    """Freeze the per-Trial evidence catalog the Graders will see."""

    del run  # outcome already aggregates Run fields; we only reach back for events/steps
    items: list[EvidenceItem] = []

    # --- expected side (no DB needed; comes from the versioned case) ---
    scenario = case.scenario
    items.append(_item(
        trial_id=trial_id, kind=EvidenceKind.REQUEST_CONSTRAINTS,
        source_type="expected", source_id="scenario.user_request",
        projection={"user_request": scenario.user_request,
                    "hint_intent": scenario.hint_intent,
                    "replan_mode": scenario.replan_mode,
                    "planning_date": scenario.planning_date.isoformat()},
    ))
    items.append(_item(
        trial_id=trial_id, kind=EvidenceKind.PROFILE_PROJECTION,
        source_type="expected", source_id="scenario.profile",
        projection=(scenario.profile.model_dump(mode="json") if scenario.profile else {}),
    ))
    items.append(_item(
        trial_id=trial_id, kind=EvidenceKind.EXPECTED_OUTCOME,
        source_type="expected", source_id="case.expected_outcome",
        projection=case.expected_outcome.model_dump(mode="json"),
    ))
    items.append(_item(
        trial_id=trial_id, kind=EvidenceKind.TRAJECTORY_POLICY,
        source_type="expected", source_id="case.trajectory_policy",
        projection=case.trajectory_policy.model_dump(mode="json"),
    ))
    items.append(_item(
        trial_id=trial_id, kind=EvidenceKind.TOOL_ALLOWLIST,
        source_type="expected", source_id="case.tool_allowlist",
        projection={"allowlist": ["memory_lookup", "rag_retrieve", "web_search"]},
    ))

    # --- runtime outcome projection ---
    items.append(_item(
        trial_id=trial_id, kind=EvidenceKind.OUTCOME_STATUS,
        source_type="run", source_id=str(outcome.run_id),
        projection={
            "status": outcome.status, "result_kind": outcome.result_kind,
            "final_plan_id": str(outcome.final_plan_id) if outcome.final_plan_id else None,
            "error_code": outcome.error_code,
            "fallback_reason": outcome.fallback_reason,
            "user_id": str(outcome.user_id),
        },
    ))
    items.append(_item(
        trial_id=trial_id, kind=EvidenceKind.RUN_METRICS,
        source_type="run", source_id=str(outcome.run_id),
        projection={
            "tokens_in": outcome.total_tokens_in,
            "tokens_out": outcome.total_tokens_out,
            "latency_ms": outcome.total_latency_ms,
            "terminal_event_count": sum(
                1 for e in outcome.events
                if e.get("event_type") in {"run.completed", "run.degraded",
                                            "run.failed", "run.cancelled"}
            ),
        },
    ))
    items.append(_item(
        trial_id=trial_id, kind=EvidenceKind.TRANSCRIPT_HASH,
        source_type="run", source_id=str(outcome.run_id),
        projection={"transcript_hash": outcome.transcript_hash},
    ))

    # --- plan + task projections (when a plan was produced) ---
    if outcome.final_plan_id is not None:
        plan = await session.get(Plan, outcome.final_plan_id)
        if plan is not None and plan.user_id == outcome.user_id:
            # Evidence refs recorded by the Finalizer are stored on Plan.
            persisted_refs = list(plan.evidence_refs_json or [])
            items.append(_item(
                trial_id=trial_id, kind=EvidenceKind.PLAN_PROJECTION,
                source_type="plan", source_id=str(plan.id),
                projection={**_plan_projection(plan),
                            "evidence_refs": persisted_refs},
            ))
            plan_tasks = await session.scalars(
                select(Task).where(Task.plan_id == plan.id).order_by(Task.order_index)
            )
            for task in plan_tasks:
                items.append(_item(
                    trial_id=trial_id, kind=EvidenceKind.TASK_PROJECTION,
                    source_type="task", source_id=str(task.id),
                    projection={**_task_projection(task),
                                "user_id": str(task.user_id)},
                ))

    # --- step / event / tool projections (transcript-derived facts) ---
    for step in outcome.steps:
        # outcome.steps are dicts already projected by ``_step_projection``.
        # PCA-1 / PR-9c.2 Stage B: nodes that retry (e.g. ``rule_validator``
        # on repair-path cases) produce one AgentStep per attempt; using
        # just the node name as ``source_id`` collided on the
        # ``uq_eval_evidence_items_trial_kind_source`` UNIQUE constraint.
        # Mirror the tool_call-projection hotfix by folding the attempt
        # index into ``source_id`` so re-attempts no longer collide.
        node_name = str(step.get("node"))
        attempt = step.get("attempt")
        source_id = (
            f"{node_name}#attempt{attempt}"
            if attempt is not None
            else node_name
        )
        items.append(_item(
            trial_id=trial_id, kind=EvidenceKind.STEP_PROJECTION,
            source_type="step", source_id=source_id,
            projection=step,
        ))
    for event in outcome.events:
        items.append(_item(
            trial_id=trial_id, kind=EvidenceKind.EVENT_PROJECTION,
            source_type="event", source_id=str(event.get("sequence")),
            projection=event,
        ))
    for tool_call in outcome.tool_calls:
        # PCA-1 hotfix: prefer the projected ``id`` (unique per tool_call
        # row) over ``tool_name`` so re-attempts of the same tool no longer
        # collide on uq_eval_evidence_items_trial_kind_source.
        source_id = str(tool_call.get("id") or tool_call.get("tool_name"))
        items.append(_item(
            trial_id=trial_id, kind=EvidenceKind.TOOL_CALL_PROJECTION,
            source_type="tool_call", source_id=source_id,
            projection=tool_call,
        ))

    # PR-8b: emit a synthetic EXPECTED_CITATIONS_MAP evidence so the
    # evidence_citation_precision/recall sub-grader can translate the
    # expected_citations=["mem-A", ...] strings (set on the Case) into
    # the planted Memory UUIDs that PlanCandidate.evidence_refs actually
    # carries. Always emits a row, even with an empty map, so a grader
    # can distinguish "no planted memories" from "missing data".
    planted_rows = (
        await session.scalars(
            select(Memory).where(
                Memory.user_id == outcome.user_id,
                Memory.status == "active",
            )
        )
    ).all()
    fixture_map: dict[str, str] = {}
    for mem in planted_rows:
        raw_content = mem.content_json if isinstance(mem.content_json, dict) else {}
        fmid = raw_content.get("fixture_memory_id")
        if isinstance(fmid, str) and fmid:
            fixture_map[fmid] = str(mem.id)
    items.append(_item(
        trial_id=trial_id,
        kind=EvidenceKind.EXPECTED_CITATIONS_MAP,
        source_type="planted_memory_map",
        source_id="fixture_memory_id_mapping",
        projection={"expected_citations_map": fixture_map},
    ))

    # --- risk + repair + visible evidence derived from raw rows ---
    event_rows = await session.scalars(
        select(AgentEvent)
        .where(AgentEvent.run_id == outcome.run_id)
        .order_by(AgentEvent.sequence)
    )
    event_rows_list = list(event_rows)
    items.append(_item(
        trial_id=trial_id, kind=EvidenceKind.RISK_SIGNALS,
        source_type="runtime", source_id=f"risk:{outcome.run_id}",
        projection=_risk_signals(scenario.user_request),
    ))
    # Need raw AgentStep rows for repair detection (attempt count).
    step_rows = await session.scalars(
        select(AgentStep)
        .where(AgentStep.run_id == outcome.run_id)
        .order_by(AgentStep.sequence)
    )
    step_rows_list = list(step_rows)
    items.append(_item(
        trial_id=trial_id, kind=EvidenceKind.REPAIR_SIGNAL,
        source_type="runtime", source_id=f"repair:{outcome.run_id}",
        projection=_repair_signal(event_rows_list, step_rows_list),
    ))

    # Independent source: the exact provider-call visibility window frozen in
    # the candidate-producing AgentStep trace, never the persisted Plan refs.
    items.append(_item(
        trial_id=trial_id, kind=EvidenceKind.EVIDENCE_VISIBLE_REFS,
        source_type="runtime", source_id=f"visible_refs:{outcome.run_id}",
        projection=_visible_refs_from_steps(step_rows_list),
    ))

    # cross_user_signal: scan plan/tasks for any user_id != outcome.user_id.
    foreign_users: list[str] = []
    if outcome.final_plan_id is not None:
        plan = await session.get(Plan, outcome.final_plan_id)
        if plan is not None:
            plan_user = str(plan.user_id)
            if plan_user != str(outcome.user_id):
                foreign_users.append(plan_user)
            plan_tasks = await session.scalars(
                select(Task).where(Task.plan_id == plan.id)
            )
            for task in plan_tasks:
                task_user = str(task.user_id)
                if task_user != str(outcome.user_id) and task_user not in foreign_users:
                    foreign_users.append(task_user)
    items.append(_item(
        trial_id=trial_id, kind=EvidenceKind.CROSS_USER_SIGNAL,
        source_type="runtime", source_id=f"cross_user:{outcome.run_id}",
        projection={"foreign_user_ids": foreign_users},
    ))

    # redacted_output: companion message if any (truncated, no PII scrub of body)
    companion_proj = _find_companion_for(event_rows_list)
    if companion_proj is not None:
        items.append(_item(
            trial_id=trial_id, kind=EvidenceKind.REDACTED_OUTPUT,
            source_type="runtime", source_id=f"companion:{outcome.run_id}",
            projection=companion_proj,
        ))

    # PR-5: aggregate provider-call audit rows into a single structured
    # evidence item. Raw transcripts are NOT included (issue #6 + spec
    # Model-domain "raw Provider transcript" denied list) -- the projection
    # carries the deterministic metadata a grader needs without leaking PII.
    provider_calls = outcome.provider_calls
    if provider_calls:
        per_method_counts: dict[str, int] = {}
        per_kind_counts: dict[str, int] = {}
        total_in = 0
        total_out = 0
        failed = 0
        max_latency = 0
        for call in provider_calls:
            kind = str(call.get("provider_kind"))
            method = str(call.get("provider_method"))
            per_kind_counts[kind] = per_kind_counts.get(kind, 0) + 1
            per_method_counts[method] = per_method_counts.get(method, 0) + 1
            ti = call.get("tokens_in")
            to = call.get("tokens_out")
            if isinstance(ti, int):
                total_in += ti
            if isinstance(to, int):
                total_out += to
            if str(call.get("status")) == "error":
                failed += 1
            lat = call.get("latency_ms")
            if isinstance(lat, int) and lat > max_latency:
                max_latency = lat
        items.append(_item(
            trial_id=trial_id,
            kind=EvidenceKind.PROVIDER_CALL_PROJECTION,
            source_type="provider_calls",
            source_id=str(outcome.run_id),
            projection={
                "call_count": len(provider_calls),
                "per_method_counts": per_method_counts,
                "per_kind_counts": per_kind_counts,
                "total_tokens_in": total_in,
                "total_tokens_out": total_out,
                "failed_calls": failed,
                "latency_ms_max": max_latency,
                "calls": [
                    {
                        "sequence": c.get("sequence"),
                        "kind": c.get("provider_kind"),
                        "method": c.get("provider_method"),
                        "retry_attempt": c.get("retry_attempt"),
                        "status": c.get("status"),
                        "error_code": c.get("error_code"),
                        "tokens_in": c.get("tokens_in"),
                        "tokens_out": c.get("tokens_out"),
                        "model_id": c.get("model_id"),
                    }
                    for c in provider_calls
                ],
            },
        ))

    return items
