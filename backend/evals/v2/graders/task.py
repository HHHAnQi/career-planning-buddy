"""Task-domain deterministic Graders.

Spec gate (PR-4): intent/result_kind + allowed_run_status are unconditional
hard gates; the plan-derived sub-graders (horizon/task_count/budget/
startability/deliverable) become ``not_applicable`` when the run legitimately
did not produce a plan (clarification / safe_response). The Task Grader NEVER
auto-passes candidate=None as in the legacy runner -- expected=plan + no plan
yields explicit hard-gate failures on intent_result_kind / allowed_run_status.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from evals.v2.collectors.outcome import RunOutcome
from evals.v2.contracts import GradeResult
from evals.v2.graders.base import (
    AuthorizedView,
    EvidenceKind,
    as_dict_list,
    as_int,
)

if TYPE_CHECKING:
    from evals.v2.contracts import EvalCase

GRADER_NAME_PREFIX = "task"
GRADER_VERSION = "v1"
ALLOWED_KINDS = frozenset({
    EvidenceKind.REQUEST_CONSTRAINTS,
    EvidenceKind.PROFILE_PROJECTION,
    EvidenceKind.EXPECTED_OUTCOME,
    EvidenceKind.PLAN_PROJECTION,
    EvidenceKind.TASK_PROJECTION,
    EvidenceKind.OUTCOME_STATUS,
})


def _boolean_grade(
    *, name: str, passed: bool, actual: object, expected: object,
    evidence_ids: list[UUID], rationale: str,
) -> GradeResult:
    return GradeResult(
        grader_name=f"{GRADER_NAME_PREFIX}.{name}",
        grader_version=GRADER_VERSION,
        domain="task",
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
        domain="task",
        metric_type="categorical",
        categorical_value="not_applicable", hard_gate=False,
        evidence_item_ids=evidence_ids,
        evidence={"actual": None, "expected": None, "subgrader": name, "reason": "na"},
        rationale=rationale,
    )


def _horizon_in_window(plan: dict[str, object]) -> bool:
    """Every scheduled date must fall in the current seven-day action window."""

    try:
        start = date.fromisoformat(str(plan.get("plan_date", plan["horizon_start"])))
        horizon_end = date.fromisoformat(str(plan["horizon_end"]))
        end = min(start + timedelta(days=6), horizon_end)
        tasks = as_dict_list(plan.get("tasks", []))
        if not tasks:
            return False
        for task in tasks:
            if not isinstance(task, dict):
                return False
            scheduled = task.get("scheduled_date")
            if scheduled is None:
                return False
            d = date.fromisoformat(str(scheduled))
            if not (start <= d <= end):
                return False
        return True
    except (KeyError, ValueError, TypeError):
        return False


def _within_budget(tasks: list[dict[str, object]], budget: int | None) -> bool:
    if not tasks:
        return False
    if budget is None:
        return True
    per_day: dict[str, int] = {}
    for task in tasks:
        scheduled = str(task.get("scheduled_date", ""))
        per_day[scheduled] = per_day.get(scheduled, 0) + as_int(
            task.get("estimated_minutes", 0)
        )
    return all(total <= budget for total in per_day.values())


def _startability(tasks: list[dict[str, object]]) -> bool:
    if not tasks:
        return False
    return all(str(t.get("starter_action", "")).strip() != "" for t in tasks if isinstance(t, dict))


def _deliverable(tasks: list[dict[str, object]]) -> bool:
    if not tasks:
        return False
    return all(str(t.get("deliverable", "")).strip() != "" for t in tasks if isinstance(t, dict))


async def grade(outcome: RunOutcome, view: AuthorizedView, expected: EvalCase) -> list[GradeResult]:
    from evals.v2.contracts import PlanningEvalScenario

    if not isinstance(expected.scenario, PlanningEvalScenario):
        raise TypeError("planning task grader requires a planning scenario")
    expected_outcome = expected.expected_outcome
    scenario = expected.scenario
    profile_item = view.first(EvidenceKind.PROFILE_PROJECTION)
    profile_id = profile_item.id if profile_item is not None else None
    expected_item = view.first(EvidenceKind.EXPECTED_OUTCOME)
    expected_id = expected_item.id if expected_item is not None else None
    plan_item = view.first(EvidenceKind.PLAN_PROJECTION)
    plan_id = plan_item.id if plan_item is not None else None
    task_items = view.items(EvidenceKind.TASK_PROJECTION)
    task_ids = [item.id for item in task_items]

    results: list[GradeResult] = []

    # 1. intent_result_kind -- actual result_kind must equal expected.
    results.append(_boolean_grade(
        name="intent_result_kind",
        passed=outcome.result_kind == expected_outcome.result_kind,
        actual=outcome.result_kind, expected=expected_outcome.result_kind,
        evidence_ids=[expected_id] if expected_id else [],
        rationale="runtime result_kind must match the versioned expected outcome",
    ))

    # 2. allowed_run_status -- outcome.status must be in allowed_run_statuses.
    results.append(_boolean_grade(
        name="allowed_run_status",
        passed=outcome.status in set(expected_outcome.allowed_run_statuses),
        actual=outcome.status, expected=list(expected_outcome.allowed_run_statuses),
        evidence_ids=[expected_id] if expected_id else [],
        rationale="runtime status must be one of the versioned allowed statuses",
    ))

    plan_expected = expected_outcome.result_kind == "plan"
    has_plan = outcome.plan is not None and outcome.tasks

    # For the plan-derived sub-graders: when the case expected a plan and a
    # plan exists, evaluate each sub-signal. When the case expected a plan
    # but the run produced none, all five return hard ``not_applicable``
    # without auto-passing (intent + allowed_status already failed above).
    # When the case expected clarification/safe_response, the plan-derived
    # checks are structurally not applicable.
    applicable = plan_expected and has_plan

    def _projected_tasks() -> list[dict[str, object]]:
        return [dict(item.projection) for item in task_items]

    def _budget() -> int | None:
        if scenario.profile is None:
            return None
        return scenario.profile.time_budget_minutes

    def _minutes_by_day() -> dict[str, int]:
        per_day: dict[str, int] = {}
        for task in outcome.tasks:
            scheduled = str(task.get("scheduled_date", ""))
            per_day[scheduled] = per_day.get(scheduled, 0) + as_int(
                task.get("estimated_minutes", 0)
            )
        return per_day

    if applicable:
        # 3. horizon_match
        plan = outcome.plan or {}
        results.append(_boolean_grade(
            name="horizon_match",
            passed=_horizon_in_window({**plan, "tasks": outcome.tasks}),
            actual=sorted({str(t.get("scheduled_date")) for t in outcome.tasks}),
            expected=(
                "within the 7-day action window from "
                f"{plan.get('plan_date', plan.get('horizon_start'))}"
            ),
            evidence_ids=task_ids,
            rationale="every task scheduled_date must fall in the seven-day action window",
        ))
        # 4. task_count
        results.append(_boolean_grade(
            name="task_count",
            passed=1 <= len(outcome.tasks) <= 7,
            actual=len(outcome.tasks), expected="1 <= count <= 7",
            evidence_ids=task_ids,
            rationale="a plan may carry up to seven executable tasks for its action week",
        ))
        # 5. time_budget
        budget = _budget()
        results.append(_boolean_grade(
            name="time_budget",
            passed=_within_budget(outcome.tasks, budget),
            actual=_minutes_by_day(),
            expected=f"each day <= {budget}" if budget is not None else "(no profile budget)",
            evidence_ids=task_ids + ([profile_id] if profile_id else []),
            rationale="each scheduled day must fit the profile daily time budget",
        ))
        # 6. startability
        results.append(_boolean_grade(
            name="startability",
            passed=_startability(outcome.tasks),
            actual=[bool(str(t.get("starter_action", "")).strip()) for t in outcome.tasks],
            expected="every task carries a non-empty starter_action",
            evidence_ids=task_ids,
            rationale="every task must have an immediately startable action",
        ))
        # 7. deliverable
        results.append(_boolean_grade(
            name="deliverable",
            passed=_deliverable(outcome.tasks),
            actual=[bool(str(t.get("deliverable", "")).strip()) for t in outcome.tasks],
            expected="every task carries a non-empty deliverable",
            evidence_ids=task_ids,
            rationale="every task must declare a concrete deliverable",
        ))
    else:
        for name in ("horizon_match", "task_count", "time_budget", "startability", "deliverable"):
            results.append(_not_applicable(
                name, task_ids,
                "no plan projection was produced for this case",
            ))

    # 8. replan_continuity -- only applies to hint_intent=replan cases.
    if scenario.hint_intent == "replan" and applicable:
        # Legacy check: source-plan deliverables should not be re-emitted 1:1.
        # RunOutcome.tests are limited here to the new plan; we approximate by
        # requiring the new plan's tasks to have non-empty titles distinct
        # from a placeholder seed (the source plan is not part of RunOutcome).
        # Real continuity verification lands in PR-8 (counterfactual).
        replan_titles_ok = all(
            str(t.get("title", "")).strip() != ""
            for t in outcome.tasks if isinstance(t, dict)
        )
        results.append(_boolean_grade(
            name="replan_continuity",
            passed=replan_titles_ok,
            actual=[t.get("title") for t in outcome.tasks],
            expected="non-empty titles (full continuity check in PR-8)",
            evidence_ids=task_ids + ([plan_id] if plan_id else []),
            rationale="replan tasks must remain well-formed (deep continuity is a PR-8 ablation)",
        ))
    else:
        results.append(_not_applicable(
            "replan_continuity", [], "case is not in replan mode or produced no plan",
        ))

    return results
