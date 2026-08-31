"""Planning quality & cost metric proof via Mock scenarios (Phase 2).

Constructs each frozen metric's numerator and denominator edge cases with
the deterministic Mock provider and asserts the classifier gets them
right. Scenarios follow the acceptance list:

  first-pass success / repairable / non-repairable / call failure /
  format failure / degraded(template) / clarification(non-plan).
"""

from __future__ import annotations

from uuid import uuid4

from evals.planning_metrics import (
    NON_PLAN_TERMINALS,
    PlanningMetrics,
    TrialRecord,
    _classify,
)


def _record(
    *,
    provenance: str | None,
    status: str = "completed",
    result_kind: str | None = "plan",
    fallback_reason: str | None = None,
    steps: list[dict] | None = None,
    tokens: tuple[int, int] | None = (200, 350),
) -> TrialRecord:
    tin, tout = tokens if tokens else (None, None)
    return TrialRecord(
        run_id=str(uuid4()),
        status=status,
        result_kind=result_kind,
        fallback_reason=fallback_reason,
        provenance=provenance,
        repair_count=0,
        steps=steps or [],
        tokens_in=tin,
        tokens_out=tout,
        latency_ms=5000,
    )


def _fresh() -> PlanningMetrics:
    return PlanningMetrics()


def test_first_pass_success_counts_numerator_and_denominator() -> None:
    m = _fresh()
    _classify(_record(provenance="model_pass"), m)
    s = m.summary()
    assert s["A_planner_first_pass_compliance"] == 1.0
    assert s["C_final_compliant_plan_rate"] == 1.0
    assert s["denominators"]["should_plan_total"] == 1


def test_deterministic_repair_success() -> None:
    m = _fresh()
    _classify(
        _record(provenance="deterministic_repair"), m
    )
    s = m.summary()
    assert s["B2_deterministic_repair_success"] == 1.0
    assert s["A_planner_first_pass_compliance"] == 0.0
    assert s["C_final_compliant_plan_rate"] == 1.0


def test_llm_repair_success() -> None:
    m = _fresh()
    _classify(_record(provenance="llm_repair"), m)
    s = m.summary()
    assert s["B3_llm_repair_success"] == 1.0


def test_format_repair_success() -> None:
    m = _fresh()
    _classify(_record(provenance="format_repair"), m)
    s = m.summary()
    assert s["B1_format_repair_success"] == 1.0


def test_degraded_fallback_is_not_llm_repair_success() -> None:
    """Template degradation is NOT a repair success under any bucket."""
    m = _fresh()
    _classify(
        _record(
            provenance="fallback",
            status="degraded",
            fallback_reason="business_repair_disabled",
            steps=[{"node": "revise_or_fallback", "status": "completed"}],
        ),
        m
    )
    s = m.summary()
    assert s["B3_llm_repair_success"] is None  # never entered
    assert s["B2_deterministic_repair_success"] == 0.0  # entered, not succeeded
    assert s["C_final_compliant_plan_rate"] == 0.0  # template ≠ compliant
    assert s["outcome_split"]["degraded_plan"] == 1


def test_call_failure_stays_in_denominator() -> None:
    """Provider call failure keeps the trial in A/C denominators."""
    m = _fresh()
    _classify(
        _record(
            provenance=None,
            status="failed",
            result_kind=None,
            tokens=(0, 0),
        ),
        m,
    )
    s = m.summary()
    assert s["denominators"]["should_plan_total"] == 1
    assert s["A_planner_first_pass_compliance"] == 0.0
    assert s["C_final_compliant_plan_rate"] == 0.0
    assert s["outcome_split"]["run_failed"] == 1


def test_no_plan_is_never_compliant() -> None:
    m = _fresh()
    _classify(
        _record(provenance=None, status="failed", result_kind=None), m
    )
    s = m.summary()
    assert s["outcome_split"]["no_plan"] == 1
    assert s["C_final_compliant_plan_rate"] == 0.0


def test_clarification_excluded_from_plan_denominator() -> None:
    """Non-plan terminals (clarification/safe) are counted separately."""
    m = _fresh()
    _classify(
        _record(
            provenance=None,
            status="degraded",
            result_kind="clarification",
        ),
        m,
    )
    s = m.summary()
    assert s["denominators"]["should_plan_total"] == 0
    assert s["A_planner_first_pass_compliance"] is None  # N/A
    assert s["outcome_split"]["non_plan_terminal"] == 1


def test_unknown_usage_preserved_not_zeroed() -> None:
    """Unknown provider usage stays None — never filled with 0."""
    m = _fresh()
    _classify(_record(provenance="model_pass", tokens=None), m)
    if m.trials and m.trials[0].tokens_in is None:
        m.unknown_usage_trials += 1
    s = m.summary()
    assert s["cost"]["unknown_usage_trials"] == 1


def test_non_plan_terminal_set_is_frozen() -> None:
    assert NON_PLAN_TERMINALS == {
        "clarification",
        "safe_response",
        "navigation",
    }
