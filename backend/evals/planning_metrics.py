"""Planning quality & cost metrics from persisted run events (frozen v1).

Reads agent_events (run.provenance) and agent_runs for a set of trial
run_ids, and computes the pre-registered metrics from
docs/standards/metric-registry.md (Planning v1). Pure offline — no model
calls.

Chain: real executor run -> persisted events/steps/runs -> collect() ->
PlanningMetrics.summary(). This module is the READ side only; write side
is the graph's persist node.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

# Column names verified against information_schema:
#   agent_steps has created_at (NOT started_at), finished_at
#   agent_events.payload_json is JSONB containing plan_provenance as string
QUERY = text(
    """
    SELECT r.id AS run_id,
           r.status,
           r.result_kind,
           r.fallback_reason,
           r.total_tokens_in,
           r.total_tokens_out,
           r.total_latency_ms,
           (SELECT e.payload_json ->> 'plan_provenance'
              FROM agent_events e
             WHERE e.run_id = r.id AND e.event_type = 'run.provenance'
             ORDER BY e.sequence DESC LIMIT 1) AS provenance,
           (SELECT json_agg(json_build_object(
                    'node', s.node_name,
                    'status', s.status,
                    'error_code', s.error_code,
                    'trace', s.trace_data,
                    'tokens_in', s.tokens_in,
                    'tokens_out', s.tokens_out,
                    'cost_cny', s.cost_cny
                ) ORDER BY s.created_at, s.sequence)
              FROM agent_steps s WHERE s.run_id = r.id) AS steps
    FROM agent_runs r
    WHERE r.id IN (SELECT unnest(CAST(:run_ids AS uuid[])))
    """
)

NON_PLAN_TERMINALS = {"clarification", "safe_response", "navigation"}


@dataclass
class TrialRecord:
    """One trial as read back from the database."""

    run_id: str
    status: str
    result_kind: str | None
    fallback_reason: str | None
    provenance: str | None
    repair_steps: list[dict[str, Any]]
    planning_steps: list[dict[str, Any]]
    tokens_in: int | None
    tokens_out: int | None
    latency_ms: int


@dataclass
class PlanningMetrics:
    should_plan_total: int = 0
    first_pass_compliant: int = 0
    final_compliant: int = 0
    no_plan: int = 0
    degraded_plan: int = 0
    run_failed: int = 0
    non_plan_terminal: int = 0
    format_repair_entered: int = 0
    format_repair_succeeded: int = 0
    deterministic_repair_entered: int = 0
    deterministic_repair_succeeded: int = 0
    llm_repair_entered: int = 0
    llm_repair_succeeded: int = 0
    total_tokens_in: int | None = 0
    total_tokens_out: int | None = 0
    unknown_usage_trials: int = 0
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
                "unknown_usage_trials": self.unknown_usage_trials,
                "per_trial_details": [
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


def _extract_repair_entry(record: TrialRecord) -> dict[str, bool]:
    """Determine which repair types were ENTERED from step evidence.

    The revise_or_fallback node is the single repair funnel; which type
    was attempted is distinguishable from step trace data:
      - trace has 'plan_provenance' == 'format_repair' etc.
      - or the node produced a fallback_reason indicating which type ran
    """
    entered = {"format": False, "deterministic": False, "llm": False}
    for step in record.repair_steps:
        trace = step.get("trace") or {}
        prov = trace.get("plan_provenance") or record.provenance
        fb = record.fallback_reason or ""
        if prov == "format_repair" or "format" in fb:
            entered["format"] = True
        if prov == "llm_repair" or "llm" in fb or "business" in fb:
            entered["llm"] = True
    # The deterministic repair is ALWAYS the first attempt in the funnel
    # (it runs before the LLM branch in _revise_or_fallback); if the
    # revise node executed at all, deterministic was entered.
    if record.repair_steps:
        entered["deterministic"] = True
    return entered


def _classify(record: TrialRecord, metrics: PlanningMetrics) -> None:
    provenance = record.provenance

    # Non-plan terminals: reported separately, never in plan denominators.
    if record.result_kind in NON_PLAN_TERMINALS:
        metrics.non_plan_terminal += 1
        return

    metrics.should_plan_total += 1
    metrics.trials.append(record)

    # --- Outcome classification ---
    if provenance == "model_pass":
        metrics.first_pass_compliant += 1

    if record.status == "failed":
        metrics.run_failed += 1
        if record.result_kind is None:
            metrics.no_plan += 1
        # Failed runs still count repair entries if the funnel ran.
        entry = _extract_repair_entry(record)
        if entry["format"]:
            metrics.format_repair_entered += 1
        if entry["deterministic"]:
            metrics.deterministic_repair_entered += 1
        if entry["llm"]:
            metrics.llm_repair_entered += 1
        return

    if record.status == "degraded":
        metrics.degraded_plan += 1

    # Final compliance: a delivered plan that is NOT a fallback template.
    if record.result_kind == "plan" and provenance not in {"fallback", None}:
        metrics.final_compliant += 1

    # --- Repair funnel entered/succeeded ---
    # Entered: the funnel ran (any repair step) or provenance indicates it.
    # Succeeded: the trial's provenance is exactly that repair type AND
    # the final output is rule-compliant (not a fallback template).
    entry = _extract_repair_entry(record)
    is_compliant = (
        record.result_kind == "plan" and provenance not in {"fallback", None}
    )

    if provenance == "format_repair" or entry["format"]:
        metrics.format_repair_entered += 1
        if provenance == "format_repair" and is_compliant:
            metrics.format_repair_succeeded += 1
    if provenance == "deterministic_repair" or entry["deterministic"]:
        metrics.deterministic_repair_entered += 1
        if provenance == "deterministic_repair" and is_compliant:
            metrics.deterministic_repair_succeeded += 1
    if provenance == "llm_repair" or entry["llm"]:
        metrics.llm_repair_entered += 1
        if provenance == "llm_repair" and is_compliant:
            metrics.llm_repair_succeeded += 1

    # --- Cost ---
    if record.tokens_in is None:
        metrics.unknown_usage_trials += 1
    else:
        tin = metrics.total_tokens_in or 0
        tout = metrics.total_tokens_out or 0
        metrics.total_tokens_in = tin + record.tokens_in
        metrics.total_tokens_out = tout + record.tokens_out


async def collect(
    run_ids: list[str],
    *,
    database_url: str | None = None,
    session_factory: Any | None = None,
) -> PlanningMetrics:
    """Read persisted runs and compute planning metrics.

    Args:
        run_ids: UUID strings of the runs to collect.
        database_url: optional override (defaults to settings).
        session_factory: optional pre-built async_sessionmaker for
            reading within a specific transaction context (tests).
    """
    owns_engine = session_factory is None
    if session_factory is None:
        url = database_url or get_settings().database_url
        engine = create_async_engine(url)
        session_factory = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
    metrics = PlanningMetrics()
    try:
        # Normalize to UUID list for the IN clause
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

            steps = row.steps or []
            repair_steps = [
                s for s in steps if s.get("node") == "revise_or_fallback"
            ]
            planning_steps = [
                s for s in steps if s.get("node") == "career_planning_agent"
            ]
            record = TrialRecord(
                run_id=rid,
                status=row.status,
                result_kind=row.result_kind,
                fallback_reason=row.fallback_reason,
                provenance=row.provenance,
                repair_steps=repair_steps,
                planning_steps=planning_steps,
                tokens_in=row.total_tokens_in,
                tokens_out=row.total_tokens_out,
                latency_ms=row.total_latency_ms,
            )
            _classify(record, metrics)
    finally:
        if owns_engine:
            await engine.dispose()
    return metrics
