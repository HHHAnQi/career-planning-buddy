"""Pure-function tests for the Stage 4 routing decision table.

These pin the _after_* decisions that decide graph topology. They are
pread-at in the runtime integration tests; here we lock the boundary
behaviour so future refactors of state shape do not silently flip routing.
"""

from __future__ import annotations

import pytest

from app.agent.graph import FixedPlanningGraph
from app.agent.nodes import parse_horizon_weeks, risk_gate
from app.schemas.agent_runs import (
    IntentResult,
    PlanningState,
    ValidationCheck,
    ValidationReport,
)
from app.schemas.enums import ReplanMode, RunIntent


# --- _after_risk ---
def test_after_risk_routes_high_to_safe_response() -> None:
    state: PlanningState = {"risk": risk_gate("我想自杀")}
    assert FixedPlanningGraph._after_risk(state) == "high"


def test_after_risk_routes_non_high_to_intent_router() -> None:
    state: PlanningState = {"risk": risk_gate("今天天气不错")}
    assert FixedPlanningGraph._after_risk(state) == "safe"


# --- _after_intent ---
def _intent(intent: RunIntent, missing: list[str] | None = None) -> IntentResult:
    return IntentResult(
        intent=intent,
        replan_mode=ReplanMode.INITIAL,
        confidence=1,
        missing_slots=missing or [],
        effective_goal_type=None,
        requested_horizon_weeks=None,
        requires_fresh_information=False,
        method="rule",
    )


def test_after_intent_unsupported_routes_to_clarification() -> None:
    state: PlanningState = {"intent": _intent(RunIntent.UNSUPPORTED)}
    assert FixedPlanningGraph._after_intent(state) == "clarification"


def test_after_intent_missing_slots_routes_to_clarification() -> None:
    state: PlanningState = {"intent": _intent(RunIntent.CREATE_PLAN, missing=["goal_type"])}
    assert FixedPlanningGraph._after_intent(state) == "clarification"


def test_after_intent_ready_routes_to_context_builder() -> None:
    state: PlanningState = {"intent": _intent(RunIntent.CREATE_PLAN)}
    assert FixedPlanningGraph._after_intent(state) == "ready"


# --- _after_validation ---
def _validation_report(passed: bool) -> ValidationReport:
    return ValidationReport(
        passed=passed,
        checks=[ValidationCheck(code="X", passed=passed, message="m")],
        repair_instructions=[] if passed else ["Repair X"],
    )


def test_after_validation_passed_goes_to_companion() -> None:
    state: PlanningState = {"validation_report": _validation_report(True)}
    assert FixedPlanningGraph._after_validation(state) == "passed"


def test_after_validation_failed_without_fallback_goes_to_repair() -> None:
    state: PlanningState = {
        "validation_report": _validation_report(False),
        "fallback_reason": None,
    }
    assert FixedPlanningGraph._after_validation(state) == "repair"


def test_after_validation_failed_but_already_fallback_skips_repair() -> None:
    # 关键边界：fallback_reason 已存在时即使 validator 再失败也不应再循环回 repair
    state: PlanningState = {
        "validation_report": _validation_report(False),
        "fallback_reason": "business_repair_exhausted",
    }
    assert FixedPlanningGraph._after_validation(state) == "passed"


@pytest.mark.parametrize(
    ("message", "expected_weeks"),
    [
        ("帮我制定两周计划", 2),
        ("规划接下来半个月", 2),
        ("规划未来一个月", 4),
        ("制定两个月计划", 8),
        ("制定未来3个月计划", 8),
    ],
)
def test_parse_horizon_weeks_supports_chinese_week_and_month_phrases(
    message: str,
    expected_weeks: int,
) -> None:
    assert parse_horizon_weeks(message) == expected_weeks
