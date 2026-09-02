"""Planning quality & cost metrics from persisted run events (frozen v1).

Reads agent_events (run.provenance, including repair_stages) and
agent_runs. Computes the pre-registered metrics from
docs/standards/metric-registry.md (Planning v1).

Stage-level evidence: the graph records repair_stages in the
run.provenance event payload. Each entry is
{"stage": ..., "action": "attempted"|"succeeded"|"failed"|"skipped_disabled"|"skipped_budget"}.
The collector reads these DIRECTLY — never infers from fallback_reason
keywords or final provenance alone.

Cost accumulation is INDEPENDENT of outcome classification: failed runs
keep their tokens; unknown usage stays unknown.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

QUERY = text(
    """
    SELECT r.id AS run_id,
           r.status,
           r.result_kind,
           r.fallback_reason,
           r.total_tokens_in,
           r.total_tokens_out,
           r.total_latency_ms,
           (SELECT e.payload_json
              FROM agent_events e
             WHERE e.run_id = r.id AND e.event_type = 'run.provenance'
             ORDER BY e.sequence DESC LIMIT 1) AS provenance_payload,
           (SELECT s.trace_data -> 'repair_stages'
              FROM agent_steps s
             WHERE s.run_id = r.id
               AND s.status = 'failed'
               AND s.trace_data ? 'repair_stages'
             ORDER BY s.created_at DESC LIMIT 1) AS step_repair_stages,
           (SELECT json_agg(json_build_object(
                    'node', s.node_name,
                    'status', s.status,
                    'error_code', s.error_code,
                    'trace', s.trace_data,
                    'tokens_in', s.tokens_in,
                    'tokens_out', s.tokens_out,
                    'format_repair_tokens_in',
                        (s.trace_data ->> 'format_repair_tokens_in')::int,
                    'format_repair_tokens_out',
                        (s.trace_data ->> 'format_repair_tokens_out')::int
                ) ORDER BY s.created_at, s.sequence)
              FROM agent_steps s WHERE s.run_id = r.id) AS steps
    FROM agent_runs r
    WHERE r.id IN (SELECT unnest(CAST(:run_ids AS uuid[])))
    """
)

NON_PLAN_TERMINALS = {"clarification", "safe_response", "navigation"}

# Stage names in repair_stages payloads
STAGE_FORMAT = "format_repair"
STAGE_DETERMINISTIC = "deterministic_repair"
STAGE_LLM = "llm_repair"


@dataclass
class TrialRecord:
    run_id: str
    status: str
    result_kind: str | None
    fallback_reason: str | None
    provenance: str | None
    repair_stages: list[dict[str, str]]
    all_steps: list[dict[str, Any]]
    tokens_in: int | None
    tokens_out: int | None
    latency_ms: int
    format_repair_tokens_in: int
    format_repair_tokens_out: int


@dataclass
class PlanningMetrics:
    # Denominators
    should_plan_total: int = 0
    format_repair_entered: int = 0
    deterministic_repair_entered: int = 0
    llm_repair_entered: int = 0
    llm_repair_skipped: int = 0

    # Numerators
    first_pass_compliant: int = 0
    format_repair_succeeded: int = 0
    deterministic_repair_succeeded: int = 0
    llm_repair_succeeded: int = 0
    final_compliant: int = 0

    # Outcome split (for reporting, some may overlap)
    no_plan: int = 0
    degraded_plan: int = 0
    run_failed: int = 0
    non_plan_terminal: int = 0

    # Cost (independent of outcome)
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    unknown_usage_trials: int = 0
    planning_tokens: int = 0
    repair_tokens: int = 0
    format_repair_tokens_in: int = 0
    format_repair_tokens_out: int = 0

    trials: list[TrialRecord] = field(default_factory=list)
    missing_runs: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        def _rate(num: int, den: int) -> float | None:
            return round(num / den, 4) if den else None

        return {
            "A_planner_first_pass_compliance": _rate(
                self.first_pass_compliant, self.should_plan_total
            ),
            "B1_format_repair_success": _rate(
                self.format_repair_succeeded, self.format_repair_entered
            ),
            "B2_deterministic_repair_success": _rate(
                self.deterministic_repair_succeeded,
                self.deterministic_repair_entered,
            ),
            "B3_llm_repair_success": _rate(
                self.llm_repair_succeeded, self.llm_repair_entered
            ),
            "C_final_compliant_plan_rate": _rate(
                self.final_compliant, self.should_plan_total
            ),
            "denominators": {
                "should_plan_total": self.should_plan_total,
                "format_repair_entered": self.format_repair_entered,
                "deterministic_repair_entered": self.deterministic_repair_entered,
                "llm_repair_entered": self.llm_repair_entered,
                "llm_repair_skipped": self.llm_repair_skipped,
            },
            "outcome_split": {
                "first_pass_compliant": self.first_pass_compliant,
                "final_compliant": self.final_compliant,
                "no_plan": self.no_plan,
                "degraded_plan": self.degraded_plan,
                "run_failed": self.run_failed,
                "non_plan_terminal": self.non_plan_terminal,
            },
            "cost": {
                "total_tokens_in": self.total_tokens_in,
                "total_tokens_out": self.total_tokens_out,
                "planning_tokens": self.planning_tokens,
                "repair_tokens": self.repair_tokens,
                "format_repair_tokens_in": self.format_repair_tokens_in,
                "format_repair_tokens_out": self.format_repair_tokens_out,
                "unknown_usage_trials": self.unknown_usage_trials,
                "consistency_check": (
                    self.planning_tokens + self.repair_tokens
                    == self.total_tokens_in
                ),
                "per_trial": [
                    {
                        "run_id": t.run_id,
                        "tokens_in": t.tokens_in,
                        "tokens_out": t.tokens_out,
                        "latency_ms": t.latency_ms,
                    }
                    for t in self.trials
                ],
            },
            "missing_runs": self.missing_runs,
        }


def _has_stage(stages: list[dict[str, str]], stage: str, action: str) -> bool:
    return any(s.get("stage") == stage and s.get("action") == action for s in stages)


def _entered_stage(stages: list[dict[str, str]], stage: str) -> bool:
    """A stage was ENTERED when an 'attempted' record exists."""
    return _has_stage(stages, stage, "attempted")


def _classify(record: TrialRecord, metrics: PlanningMetrics) -> None:
    provenance = record.provenance
    stages = record.repair_stages

    # Non-plan terminals: excluded from plan denominators
    if record.result_kind in NON_PLAN_TERMINALS:
        metrics.non_plan_terminal += 1
        _accumulate_cost(record, metrics)
        return

    metrics.should_plan_total += 1
    metrics.trials.append(record)

    # === Outcome classification (independent of repair stages) ===
    if provenance == "model_pass":
        metrics.first_pass_compliant += 1

    if record.status == "failed":
        metrics.run_failed += 1
        if record.result_kind is None:
            metrics.no_plan += 1
    elif record.status == "degraded":
        metrics.degraded_plan += 1

    # Final compliance: delivered plan that is NOT a fallback template
    if record.result_kind == "plan" and provenance not in {"fallback", None}:
        metrics.final_compliant += 1

    # === Repair funnel (from repair_stages, NOT keyword inference) ===
    # Format repair
    if _entered_stage(stages, STAGE_FORMAT):
        metrics.format_repair_entered += 1
        if _has_stage(stages, STAGE_FORMAT, "succeeded"):
            metrics.format_repair_succeeded += 1

    # Deterministic repair
    if _entered_stage(stages, STAGE_DETERMINISTIC):
        metrics.deterministic_repair_entered += 1
        if _has_stage(stages, STAGE_DETERMINISTIC, "succeeded"):
            metrics.deterministic_repair_succeeded += 1

    # LLM repair: entered ONLY when 'attempted' (not skipped)
    if _entered_stage(stages, STAGE_LLM):
        metrics.llm_repair_entered += 1
        if _has_stage(stages, STAGE_LLM, "succeeded"):
            metrics.llm_repair_succeeded += 1
    elif _has_stage(stages, STAGE_LLM, "skipped_disabled") or _has_stage(
        stages, STAGE_LLM, "skipped_budget"
    ):
        metrics.llm_repair_skipped += 1

    # === Cost (always, independent of outcome) ===
    _accumulate_cost(record, metrics)


def _accumulate_cost(record: TrialRecord, metrics: PlanningMetrics) -> None:
    """Cost accumulation runs for EVERY trial, including failures.

    Format repair tokens are read from the career_planning_agent step's
    trace_data (format_repair_tokens_in/out) — recorded separately from
    the step's total, which includes both planning and format repair.
    Repair-node tokens are the full revise_or_fallback step totals.
    """
    if record.tokens_in is None:
        metrics.unknown_usage_trials += 1
        return
    metrics.total_tokens_in += record.tokens_in
    tout = record.tokens_out or 0
    metrics.total_tokens_out += tout

    for step in record.all_steps:
        node = step.get("node", "")
        step_tin = step.get("tokens_in") or 0
        if node == "career_planning_agent":
            # Format repair tokens are recorded SEPARATELY in the step
            # trace; subtract them to get pure planning tokens.
            fmt_tin = step.get("format_repair_tokens_in") or 0
            metrics.planning_tokens += step_tin - fmt_tin
            metrics.format_repair_tokens_in += fmt_tin
            fmt_tout = step.get("format_repair_tokens_out") or 0
            metrics.format_repair_tokens_out += fmt_tout
        elif node == "revise_or_fallback":
            metrics.repair_tokens += step_tin


async def collect(
    run_ids: list[str],
    *,
    database_url: str | None = None,
    session_factory: Any | None = None,
) -> PlanningMetrics:
    """Read persisted runs and compute planning metrics."""
    owns_engine = session_factory is None
    if session_factory is None:
        url = database_url or get_settings().database_url
        engine = create_async_engine(url)
        session_factory = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
    metrics = PlanningMetrics()
    try:
        uuid_list = [str(rid) for rid in run_ids]
        async with session_factory() as session:
            rows = (
                await session.execute(QUERY, {"run_ids": uuid_list})
            ).all()
        by_id = {str(row.run_id): row for row in rows}

        for rid in uuid_list:
            row = by_id.get(rid)
            if row is None:
                metrics.missing_runs.append(rid)
                metrics.should_plan_total += 1
                metrics.run_failed += 1
                continue

            # Extract provenance string and repair_stages from payload
            payload = row.provenance_payload or {}
            provenance = payload.get("plan_provenance")
            repair_stages = payload.get("repair_stages") or []
            if not isinstance(repair_stages, list):
                repair_stages = []
            # Fallback: for FAILED Runs (no provenance event), read
            # repair_stages from the failed step's trace_data — this is
            # the issue-A persistence mechanism.
            if not repair_stages and row.step_repair_stages:
                step_stages = row.step_repair_stages
                if isinstance(step_stages, list):
                    repair_stages = step_stages

            steps = row.steps or []
            # Sum format repair tokens from planning-agent step traces
            fmt_tin = sum(
                (st.get("format_repair_tokens_in") or 0)
                for st in steps
                if st.get("node") == "career_planning_agent"
            )
            fmt_tout = sum(
                (st.get("format_repair_tokens_out") or 0)
                for st in steps
                if st.get("node") == "career_planning_agent"
            )
            record = TrialRecord(
                run_id=rid,
                status=row.status,
                result_kind=row.result_kind,
                fallback_reason=row.fallback_reason,
                provenance=provenance,
                repair_stages=repair_stages,
                all_steps=steps,
                tokens_in=row.total_tokens_in,
                tokens_out=row.total_tokens_out,
                latency_ms=row.total_latency_ms,
                format_repair_tokens_in=fmt_tin,
                format_repair_tokens_out=fmt_tout,
            )
            _classify(record, metrics)
    finally:
        if owns_engine:
            await engine.dispose()
    return metrics
