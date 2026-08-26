"""Fault injection: an unknown rule code must be observed, escalated to the
LLM repair arm, classified by the model, and degrade safely when repair
fails. These tests prove the unknown-rule loop is executable end-to-end
(not a paper mechanism) using a synthetic rule code that no deterministic
repair covers.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from app.agent.graph import FixedPlanningGraph
from app.core.config import get_settings
from app.core.metrics import AGENT_UNKNOWN_RULE, make_labels
from app.harness.budget import BudgetGuard, CancellationToken
from app.harness.snapshots import SnapshotService
from app.schemas.agent_runs import (
    IntentResult,
    PlanCandidate,
    PlanningContext,
    PlanningState,
    ProviderUsage,
    RunRequestSnapshot,
    ValidationCheck,
    ValidationReport,
)
from app.schemas.enums import GoalType, ReplanMode, RunIntent
from tests.test_agent_nodes import candidate

INJECTED_CODE = "INJECTED_UNKNOWN_RULE"


class _RecordingProvider:
    """Minimal PlanningProvider stand-in that records repair invocations."""

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.repair_calls = 0

    async def repair_business_rules(self, **_kwargs: Any) -> dict[str, Any]:
        self.repair_calls += 1
        return self.response


def _usage() -> dict[str, Any]:
    return ProviderUsage(
        model_id="mock",
        provider="mock",
        tokens_in=100,
        tokens_out=50,
        latency_ms=10,
    ).model_dump(mode="json")


def _state(plan: PlanCandidate, context: PlanningContext) -> PlanningState:
    config = SnapshotService.build_config(get_settings())
    return {
        "run_id": uuid4(),
        "user_id": uuid4(),
        "request": RunRequestSnapshot(
            message="帮我基于上周复盘重排计划",
            hint_intent=None,
            goal_type_override=None,
            source_plan_id=None,
        ),
        "runtime_config": config,
        "intent": IntentResult(
            intent=RunIntent.REPLAN,
            replan_mode=ReplanMode.ADJUST,
            confidence=0.9,
            missing_slots=[],
            effective_goal_type=GoalType.AGENT_APP,
            requires_fresh_information=False,
            references_past_context=True,
            method="rule",
        ),
        "planning_context": context,
        "candidate_plan": plan,
        "validation_report": ValidationReport(
            passed=False,
            checks=[
                ValidationCheck(
                    code=INJECTED_CODE,
                    passed=False,
                    message="fault-injected violation no deterministic rule covers",
                )
            ],
            repair_instructions=["INJECTED_UNKNOWN_RULE: injected failure"],
        ),
        "evidence_catalog": [],
        "repair_count": 0,
    }


def _graph(provider: _RecordingProvider) -> FixedPlanningGraph:
    config = SnapshotService.build_config(get_settings())
    budget = BudgetGuard(
        config,
        datetime.now(UTC) + timedelta(seconds=60),
        CancellationToken(),
    )
    graph = object.__new__(FixedPlanningGraph)
    graph._provider = provider  # noqa: SLF001 - fault-injection harness
    graph._budget = budget
    return graph


@pytest.mark.asyncio
async def test_unknown_rule_is_counted_escalated_and_degrades_safely() -> None:
    plan, context = candidate()
    # Repair arm returns unparseable output -> the run must degrade to the
    # deterministic fallback template instead of emitting an invalid plan.
    provider = _RecordingProvider({"_raw_text": "not a plan", "usage": _usage()})
    graph = _graph(provider)
    state = _state(plan, context)
    label = make_labels(code=INJECTED_CODE)
    before = AGENT_UNKNOWN_RULE.values.get(label, 0)

    output = await graph._revise_or_fallback(state)  # noqa: SLF001
    repaired_plan, fallback_reason, _visibility = output.value

    assert AGENT_UNKNOWN_RULE.values.get(label, 0) == before + 1
    assert state["unknown_rule_codes"] == [INJECTED_CODE]
    assert provider.repair_calls == 1, "unknown rule must escalate to LLM repair"
    assert fallback_reason == "business_repair_invalid"
    assert repaired_plan is not None, "degraded template must stay schema-valid"


@pytest.mark.asyncio
async def test_unknown_rule_llm_repair_success_records_category() -> None:
    plan, context = candidate()
    provider = _RecordingProvider(
        {
            "candidate": plan.model_dump(mode="json"),
            "usage": _usage(),
            "violation_category": "schema_shape",
        }
    )
    graph = _graph(provider)
    state = _state(plan, context)

    output = await graph._revise_or_fallback(state)  # noqa: SLF001
    _repaired_plan, fallback_reason, _visibility = output.value

    assert fallback_reason is None
    assert state["plan_provenance"] == "llm_repair"
    assert state["violation_category"] == "schema_shape"
