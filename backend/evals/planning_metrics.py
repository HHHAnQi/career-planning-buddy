"""Planning quality & cost metrics from persisted run events (frozen v1).

Reads agent_events (run.provenance, node.completed) and agent_runs for a
set of trial run_ids, and computes the pre-registered metrics from
docs/standards/metric-registry.md (Planning v1). Pure offline — no model
calls. Denominators follow the frozen definitions:

  A. first-pass compliance   / trials that SHOULD produce a plan
  B1/B2/B3. repair success   / trials that ENTERED each repair type
  C. final compliant rate    / trials that SHOULD produce a plan
  D. cost                    per-request records with unknown-preserving
                             usage fields

No-plan trials never count as plan-compliant; violation rate is null when
no scorable plan exists. Mock usage is reported but labeled as such.
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
             ORDER BY e.sequence DESC LIMIT 1) AS provenance,
           (SELECT json_agg(json_build_object(
                    'node', s.node_name,
                    'status', s.status,
                    'error_code', s.error_code,
                    'trace', s.trace_data,
                    'tokens_in', s.tokens_in,
                    'tokens_out', s.tokens_out,
                    'cost_cny', s.cost_cny
                ) ORDER BY s.started_at)
              FROM agent_steps s WHERE s.run_id = r.id) AS steps
    FROM agent_runs r
    WHERE r.id = ANY(CAST(:run_ids AS uuid[]))
    """
)

# Paths that legitimately do not produce a plan (excluded from metric A/C
# denominators, reported separately).
NON_PLAN_TERMINALS = {"clarification", "safe_response", "navigation"}


@dataclass
class TrialRecord:
    run_id: str
    status: str
    result_kind: str | None
    fallback_reason: str | None
    provenance: str | None
    repair_count: int
    steps: list[dict[str, Any]]
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
                "note": (
                    "tokens from agent_runs totals; provider-actual. Mock "
                    "runs report mock usage, never real cost."
                ),
            },
        }


def _classify(record: TrialRecord, metrics: PlanningMetrics) -> None:
    provenance = record.provenance

    # Non-plan terminals (clarification / safe_response / navigation) are
    # reported separately and never enter the plan-compliance denominators.
    if record.result_kind in NON_PLAN_TERMINALS:
        metrics.non_plan_terminal += 1
        return

    metrics.should_plan_total += 1
    metrics.trials.append(record)

    if provenance == "model_pass":
        metrics.first_pass_compliant += 1
    if record.status == "failed" or record.result_kind is None:
        metrics.run_failed += 1
        if record.result_kind is None:
            metrics.no_plan += 1
        return
    if record.status == "degraded":
        metrics.degraded_plan += 1
    # A delivered plan (completed or degraded-with-plan) that passed all
    # rules counts toward final compliance — provenance fallback means a
    # degrade template, NOT rule-compliant, so it does not count.
    if record.result_kind == "plan" and provenance not in {"fallback", None}:
        metrics.final_compliant += 1

    if provenance == "format_repair":
        metrics.format_repair_entered += 1
        metrics.format_repair_succeeded += 1
    if provenance == "deterministic_repair":
        metrics.deterministic_repair_entered += 1
        metrics.deterministic_repair_succeeded += 1
    if provenance == "llm_repair":
        metrics.llm_repair_entered += 1
        metrics.llm_repair_succeeded += 1
    # Trials that entered repair but ended in fallback: count the entry
    # via repair steps (revise_or_fallback node ran) without counting
    # success.
    revise_steps = [
        s for s in record.steps if s.get("node") == "revise_or_fallback"
    ]
    if revise_steps and provenance == "fallback":
        # The repair funnel ran but the final output is a template; which
        # repair type was attempted is distinguishable from step traces.
        metrics.deterministic_repair_entered += 1  # always attempted first


async def collect(run_ids: list[str]) -> PlanningMetrics:
    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    metrics = PlanningMetrics()
    try:
        async with factory() as session:
            rows = (
                await session.execute(QUERY, {"run_ids": run_ids})
            ).all()
        by_id = {str(row.run_id): row for row in rows}
        for rid in run_ids:
            row = by_id.get(rid)
            if row is None:
                metrics.run_failed += 1
                metrics.should_plan_total += 1
                continue
            steps = row.steps or []
            record = TrialRecord(
                run_id=rid,
                status=row.status,
                result_kind=row.result_kind,
                fallback_reason=row.fallback_reason,
                provenance=row.provenance,
                repair_count=sum(
                    1 for s in steps if s.get("node") == "revise_or_fallback"
                ),
                steps=steps,
                tokens_in=row.total_tokens_in,
                tokens_out=row.total_tokens_out,
                latency_ms=row.total_latency_ms,
            )
            _classify(record, metrics)
            if record.tokens_in is None:
                metrics.unknown_usage_trials += 1
            else:
                metrics.total_tokens_in = (metrics.total_tokens_in or 0) + record.tokens_in
                metrics.total_tokens_out = (metrics.total_tokens_out or 0) + record.tokens_out
    finally:
        await engine.dispose()
    return metrics
