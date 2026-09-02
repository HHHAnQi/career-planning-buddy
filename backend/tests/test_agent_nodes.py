"""Deterministic Stage 2 schema, node, budget, and Tool registry tests."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.agent.errors import BudgetExceededError
from app.agent.graph import FixedPlanningGraph
from app.agent.nodes import (
    build_clarification,
    build_planning_context,
    fallback_candidate,
    risk_gate,
    route_intent,
    validate_candidate,
)
from app.core.config import get_settings
from app.core.time import product_today
from app.harness.budget import BudgetGuard, CancellationToken
from app.harness.snapshots import SnapshotService
from app.schemas.agent_runs import (
    PlanCandidate,
    PlanningContext,
    PlanningState,
    ProfileContext,
    TaskCandidate,
    WeeklyFocusCandidate,
)
from app.schemas.enums import CareerStage, GoalType, ReplanMode, RunIntent, SkillLevel, TaskType
from app.tools.registry import ToolRegistry


def test_fallback_reason_ends_the_bounded_repair_loop() -> None:
    plan, context = candidate()
    invalid = plan.model_copy(
        update={
            "tasks": [
                task.model_copy(update={"estimated_minutes": context.time_budget_minutes + 1})
                for task in plan.tasks
            ]
        }
    )
    report = validate_candidate(invalid, context)
    state: PlanningState = {
        "validation_report": report,
        "fallback_reason": "business_repair_exhausted",
    }

    assert report.passed is False
    assert FixedPlanningGraph._after_validation(state) == "passed"


def profile() -> ProfileContext:
    return ProfileContext(
        user_id=uuid4(),
        version=1,
        goal_type=GoalType.AGENT_APP,
        stage=CareerStage.PREPARING,
        time_budget_minutes=60,
        skill_level=SkillLevel.INTERMEDIATE,
    )


def candidate() -> tuple[PlanCandidate, PlanningContext]:
    context = build_planning_context(
        profile=profile(),
        requested_horizon_weeks=5,
        source_plan_id=None,
        source_plan_version=None,
        completed_facts=[],
    )
    window = context.planning_window
    plan = PlanCandidate(
        plan_date=window.planning_date,
        horizon_start=window.horizon_start,
        horizon_end=window.horizon_end,
        overall_direction="完成可演示的 Agent 项目",
        weekly_focus=[
            WeeklyFocusCandidate(
                week_index=index,
                focus=f"第 {index} 周重点",
                success_signal=f"第 {index} 周产物",
            )
            for index in range(1, 6)
        ],
        summary="今天完成最小闭环",
        rationale="先形成可验证证据",
        tasks=[
            TaskCandidate(
                title=f"第 {offset + 1} 天实现一个闭环",
                task_type=TaskType.PROJECT,
                scheduled_date=window.planning_date + timedelta(days=offset),
                starter_action="打开项目并运行测试",
                deliverable=f"第 {offset + 1} 天通过的测试报告",
                estimated_minutes=60,
                rationale="服务第 1 周重点并形成第 1 周产物",
            )
            for offset in range(7)
        ],
    )
    return plan, context


def test_run_schema_is_strict_and_weekly_focus_is_contiguous() -> None:
    with pytest.raises(ValidationError):
        PlanCandidate(
            plan_date=product_today(),
            horizon_start=product_today(),
            horizon_end=product_today() + timedelta(days=7),
            overall_direction="方向",
            weekly_focus=[
                WeeklyFocusCandidate(week_index=2, focus="错误周序号", success_signal="产物")
            ],
            summary="摘要",
            rationale="原因",
            tasks=[
                TaskCandidate(
                    title="任务",
                    task_type=TaskType.OTHER,
                    scheduled_date=product_today(),
                    starter_action="开始",
                    deliverable="产物",
                    estimated_minutes=15,
                )
            ],
        )


def test_risk_intent_and_rule_validation_are_deterministic() -> None:
    assert risk_gate("我想自杀").level == "high"
    assert risk_gate("最近求职压力有点大").level == "none"
    intent = route_intent(
        message="帮我制定未来五周计划",
        hint_intent="create_plan",
        profile=profile(),
        source_plan_exists=False,
    )
    assert intent.requested_horizon_weeks == 5
    assert intent.replan_mode == ReplanMode.INITIAL
    plan, context = candidate()
    report = validate_candidate(plan, context)
    assert report.passed
    over_budget = plan.model_copy(
        update={
            "tasks": [
                plan.tasks[0].model_copy(update={"estimated_minutes": 61}),
                *plan.tasks[1:],
            ]
        }
    )
    failed = validate_candidate(over_budget, context)
    assert not failed.passed
    assert [check.code for check in failed.checks if not check.passed] == ["TIME_BUDGET"]


def test_explicit_goal_override_wins_when_replanning() -> None:
    intent = route_intent(
        message="调整后续计划",
        hint_intent="replan",
        profile=profile(),
        source_plan_exists=True,
        goal_type_override=GoalType.AI_BACKEND,
    )

    assert intent.effective_goal_type == GoalType.AI_BACKEND
    assert intent.replan_mode == ReplanMode.ADJUST


@pytest.mark.parametrize(
    ("message", "hint", "source_exists", "expected_intent", "expected_mode"),
    [
        (
            "帮我制定未来五周求职计划",
            "create_plan",
            False,
            RunIntent.CREATE_PLAN,
            ReplanMode.INITIAL,
        ),
        (
            "Create a focused four week job search plan",
            None,
            False,
            RunIntent.CREATE_PLAN,
            ReplanMode.INITIAL,
        ),
        ("任务太多，后面每天减到半小时", "replan", True, RunIntent.REPLAN, ReplanMode.ADJUST),
        (
            "Reduce the workload and retain completed progress",
            None,
            True,
            RunIntent.REPLAN,
            ReplanMode.ADJUST,
        ),
        ("不要调整，继续按原计划", "replan", True, RunIntent.REPLAN, ReplanMode.CONTINUE),
        ("Continue the current direction", None, True, RunIntent.REPLAN, ReplanMode.CONTINUE),
        ("今天有什么任务", "create_plan", False, RunIntent.NAVIGATE, ReplanMode.INITIAL),
    ],
)
def test_intent_router_uses_message_and_server_context(
    message: str,
    hint: str | None,
    source_exists: bool,
    expected_intent: RunIntent,
    expected_mode: ReplanMode,
) -> None:
    intent = route_intent(
        message=message,
        hint_intent=hint,
        profile=profile(),
        source_plan_exists=source_exists,
    )

    assert intent.intent == expected_intent
    assert intent.replan_mode == expected_mode
    assert intent.router_version == "intent-rule-v3"
    assert intent.matched_rule_ids


def test_hint_without_semantic_evidence_requires_intent_clarification() -> None:
    intent = route_intent(
        message="你好",
        hint_intent="create_plan",
        profile=profile(),
        source_plan_exists=False,
    )

    assert intent.intent == RunIntent.UNSUPPORTED
    assert intent.confidence_band == "low"
    assert intent.method == "rule_fallback"
    assert "hint_without_message_evidence" in intent.ambiguity_reasons
    assert build_clarification(intent).reason == "intent_uncertain"


def test_replan_language_without_source_does_not_create_a_plan() -> None:
    intent = route_intent(
        message="调整后续计划",
        hint_intent=None,
        profile=profile(),
        source_plan_exists=False,
    )

    assert intent.intent == RunIntent.UNSUPPORTED
    assert "replan_source_missing" in intent.ambiguity_reasons


def test_query_intent_returns_a_navigation_action() -> None:
    intent = route_intent(
        message="查看我的计划",
        hint_intent="create_plan",
        profile=profile(),
        source_plan_exists=False,
    )

    assert intent.intent == RunIntent.NAVIGATE
    assert intent.navigation_action == "view_current_plan"
    assert intent.navigation_target == "/journey"
    assert intent.confidence_band == "high"


def test_forced_replan_mode_is_authoritative_and_traceable() -> None:
    intent = route_intent(
        message="继续现有计划",
        hint_intent="create_plan",
        profile=profile(),
        source_plan_exists=True,
        forced_replan_mode=ReplanMode.ADJUST,
    )

    assert intent.intent == RunIntent.REPLAN
    assert intent.replan_mode == ReplanMode.ADJUST
    assert "server_forced_replan_mode" in intent.matched_rule_ids
    assert "hint_conflicts_with_message_or_context" in intent.ambiguity_reasons


def test_forced_replan_mode_wins_over_query_words() -> None:
    intent = route_intent(
        message="查看计划并调整下一版",
        hint_intent="replan",
        profile=profile(),
        source_plan_exists=True,
        forced_replan_mode=ReplanMode.ADJUST,
    )

    assert intent.intent == RunIntent.REPLAN
    assert intent.replan_mode == ReplanMode.ADJUST
    assert intent.navigation_target is None


def test_fallback_has_progressive_weeks_and_seven_dated_tasks() -> None:
    _, context = candidate()
    plan = fallback_candidate(context, ReplanMode.INITIAL)

    assert len({item.focus for item in plan.weekly_focus}) == 5
    assert len({item.success_signal for item in plan.weekly_focus}) == 5
    assert [task.scheduled_date for task in plan.tasks] == [
        context.planning_window.planning_date + timedelta(days=offset)
        for offset in range(7)
    ]
    assert all(task.starter_action.startswith("1. ") for task in plan.tasks)
    assert all("包含" in task.deliverable for task in plan.tasks)


def test_fallback_seven_day_tasks_stay_with_first_week_focus() -> None:
    _, context = candidate()
    plan = fallback_candidate(context, ReplanMode.INITIAL)
    first_focus = plan.weekly_focus[0].focus

    assert {task.task_type for task in plan.tasks} <= {
        TaskType.LEARNING,
        TaskType.OTHER,
    }
    assert all(task.rationale is not None for task in plan.tasks)
    assert all(first_focus in (task.rationale or "") for task in plan.tasks)
    assert not {"完成最小项目增量", "整理简历项目表达", "演练项目面试问答"} & {
        task.title for task in plan.tasks
    }


def test_repeated_weekly_focus_fails_validation() -> None:
    plan, context = candidate()
    repeated = plan.model_copy(
        update={
            "weekly_focus": [
                item.model_copy(update={"focus": "重复重点", "success_signal": "重复产物"})
                for item in plan.weekly_focus
            ]
        }
    )

    report = validate_candidate(repeated, context)

    assert report.passed is False
    assert [check.code for check in report.checks if not check.passed] == [
        "WEEKLY_FOCUS",
        "FIRST_WEEK_ALIGNMENT",
    ]


def test_daily_task_without_exact_first_week_focus_fails_alignment() -> None:
    plan, context = candidate()
    tasks = list(plan.tasks)
    tasks[3] = tasks[3].model_copy(
        update={"rationale": "提前推进后续周的简历工作"}
    )

    report = validate_candidate(plan.model_copy(update={"tasks": tasks}), context)

    assert report.passed is False
    assert [check.code for check in report.checks if not check.passed] == [
        "FIRST_WEEK_ALIGNMENT"
    ]
    assert report.repair_instructions == [
        "Every daily task rationale must include the exact week 1 focus phrase."
    ]


def test_stage2_budget_and_tool_registry_enforce_empty_tool_list() -> None:
    settings = SnapshotService.build_config(get_settings())
    guard = BudgetGuard(
        settings,
        datetime.now(UTC) + timedelta(seconds=5),
        CancellationToken(),
    )
    guard.record_llm_call(100, 100)
    with pytest.raises(BudgetExceededError, match="input_tokens"):
        guard.record_llm_call(settings.max_input_tokens_per_call + 1, 0)
    guard.claim_format_repair()
    assert guard.format_repairs == 1
    assert ToolRegistry(feature_stage=2).available_specs() == []


def test_budget_reservation_rejects_optional_call_that_cannot_fit() -> None:
    config = SnapshotService.build_config(get_settings()).model_copy(
        update={
            "max_total_tokens": 1_200,
            "max_input_tokens_per_call": 600,
            "max_output_tokens_per_call": 500,
        }
    )
    guard = BudgetGuard(
        config,
        datetime.now(UTC) + timedelta(seconds=5),
        CancellationToken(),
    )

    assert guard.can_reserve_llm_call() is True
    guard.record_llm_call(180, 320)
    assert guard.can_reserve_llm_call() is False
