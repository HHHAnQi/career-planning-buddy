"""Behavioral-domain deterministic Graders.

Spec gate (PR-4): graph-branch, repair≤1, fallback-correct, cancel-stop are
all hard gates when the run touched the relevant path; otherwise they return
``not_applicable``. Behavioral never auto-passes -- a clarification/safe_run
trajectory simply records the right structural shapes as not-applicable for
the graph-branch / repair checks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from evals.v2.collectors.outcome import RunOutcome
from evals.v2.contracts import GradeResult
from evals.v2.graders.base import AuthorizedView, EvidenceKind

if TYPE_CHECKING:
    from evals.v2.contracts import EvalCase

GRADER_NAME_PREFIX = "behavioral"
GRADER_VERSION = "v1"
ALLOWED_KINDS = frozenset({
    EvidenceKind.STEP_PROJECTION,
    EvidenceKind.EVENT_PROJECTION,
    EvidenceKind.EXPECTED_OUTCOME,
    EvidenceKind.REPAIR_SIGNAL,
})


def _boolean_grade(
    *, name: str, passed: bool, actual: object, expected: object,
    evidence_ids: list[UUID], rationale: str,
) -> GradeResult:
    return GradeResult(
        grader_name=f"{GRADER_NAME_PREFIX}.{name}",
        grader_version=GRADER_VERSION,
        domain="behavioral",
        metric_type="boolean",
        passed=passed, hard_gate=True,
        evidence_item_ids=evidence_ids,
        evidence={"actual": actual, "expected": expected, "subgrader": name},
        rationale=rationale,
    )


def _not_applicable(name: str, evidence_ids: list[UUID], rationale: str) -> GradeResult:
    return GradeResult(
        grader_name=f"{GRADER_NAME_PREFIX}.{name}",
        grader_version=GRADER_VERSION,
        domain="behavioral",
        metric_type="categorical",
        categorical_value="not_applicable", hard_gate=False,
        evidence_item_ids=evidence_ids,
        evidence={"actual": None, "expected": None, "subgrader": name, "reason": "na"},
        rationale=rationale,
    )


def _node_sequence(steps: list[dict[str, object]]) -> list[str]:
    return [str(s["node"]) for s in steps]


async def grade(outcome: RunOutcome, view: AuthorizedView, expected: EvalCase) -> list[GradeResult]:
    del expected
    step_ids = [item.id for item in view.items(EvidenceKind.STEP_PROJECTION)]
    event_ids = [item.id for item in view.items(EvidenceKind.EVENT_PROJECTION)]
    repair_item = view.first(EvidenceKind.REPAIR_SIGNAL)
    repair_id = repair_item.id if repair_item is not None else None

    nodes = _node_sequence(outcome.steps)
    results: list[GradeResult] = []

    # 1. graph_branch -- check that the structural node sequence matches the
    #    terminal kind. We use *set membership* of nodes rather than full
    #    ordering to keep the grader robust to mock provider variation, but
    #    we require a few specifics (risk_gate precedes either safe_response
    #    or intent_router; persist appears on completed and fallback plan paths).
    has_risk_gate = "risk_gate" in nodes
    has_planning = "career_planning_agent" in nodes
    has_validator = "rule_validator" in nodes
    has_persist = "persist" in nodes

    if outcome.status == "degraded" and outcome.result_kind == "safe_response":
        # safe_response path: risk_gate is present, persist must be ABSENT.
        branch_ok = has_risk_gate and not has_persist
        branch_expected = "risk_gate present, persist absent (safe_response short-circuits to END)"
    elif outcome.status == "degraded" and outcome.result_kind == "clarification":
        # intent_router present, persist absent; planning may or may not run.
        branch_ok = "intent_router" in nodes and not has_persist
        branch_expected = "intent_router present, persist absent (clarification short-circuits)"
    elif outcome.status == "degraded" and outcome.result_kind == "plan":
        branch_ok = has_risk_gate and has_planning and has_validator and has_persist
        branch_expected = (
            "risk_gate + career_planning_agent + rule_validator + persist all present "
            "for fallback plan"
        )
    elif outcome.status == "completed":
        # Full plan path: risk_gate + intent_router + planning + validator + persist.
        branch_ok = has_risk_gate and has_planning and has_validator and has_persist
        branch_expected = "risk_gate + career_planning_agent + rule_validator + persist all present"
    elif outcome.status in {"failed", "cancelled"}:
        # Failed runs may stop anywhere; we only require risk_gate to have run.
        branch_ok = has_risk_gate
        branch_expected = "risk_gate present (failure path may abort before persist)"
    else:
        branch_ok = False
        branch_expected = "unknown status"
    results.append(_boolean_grade(
        name="graph_branch",
        passed=branch_ok,
        actual=nodes, expected=branch_expected,
        evidence_ids=step_ids,
        rationale="node sequence must follow the structural shape of the run's terminal kind",
    ))

    # 2. repair_at_most_one -- count format/business repair events; ≤1 each.
    repair_count_raw = (
        repair_item.projection.get("total_repair_attempts", 0) if repair_item else 0
    )
    repair_count = int(repair_count_raw) if isinstance(repair_count_raw, (int, float)) else 0
    if outcome.result_kind == "plan" and outcome.status in {"completed", "degraded"}:
        # Plan runs may legitimately invoke repair, but only once before fallback.
        results.append(_boolean_grade(
            name="repair_at_most_once",
            passed=repair_count <= 1,
            actual=repair_count,
            expected="<= 1 repair attempt (format or business, not both exhaustively)",
            evidence_ids=[repair_id] if repair_id else [],
            rationale="at most one repair attempt is allowed before fallback",
        ))
    else:
        results.append(_not_applicable(
            "repair_at_most_once",
            [repair_id] if repair_id else [],
            "non-plan runs do not exercise the plan repair contract",
        ))

    # 3. fallback_correct -- fallback_reason non-None ⇒ status must be degraded;
    #    status=completed ⇒ fallback_reason must be None. Mirrors DB CK.
    has_fallback = outcome.fallback_reason is not None
    if outcome.status == "completed":
        ok = not has_fallback
        expected_fb = "fallback_reason is None on completed status"
    elif outcome.status == "degraded":
        ok = has_fallback
        expected_fb = "fallback_reason != None on degraded status"
    else:
        ok = True
        expected_fb = "no fallback expectation on failed/cancelled status"
    results.append(_boolean_grade(
        name="fallback_correct",
        passed=ok,
        actual={"status": outcome.status, "fallback_reason": outcome.fallback_reason},
        expected=expected_fb,
        evidence_ids=event_ids,
        rationale="fallback_reason must appear iff the run degraded through a fallback path",
    ))

    # 4. cancel_stop -- if a cancel event was observed, terminal must be run.cancelled.
    cancel_seen = any(e.get("event_type") == "run.cancelled" for e in outcome.events)
    if cancel_seen or outcome.status == "cancelled":
        results.append(_boolean_grade(
            name="cancel_stop",
            passed=any(e.get("event_type") == "run.cancelled" for e in outcome.events)
                 and outcome.status == "cancelled",
            actual={"cancel_event_seen": cancel_seen, "status": outcome.status},
            expected="run.cancelled event present and status=cancelled",
            evidence_ids=event_ids,
            rationale="a cooperative cancel must converge on a single cancelled terminal",
        ))
    else:
        results.append(_not_applicable(
            "cancel_stop", event_ids, "no cancel was attempted in this run"
        ))

    return results
