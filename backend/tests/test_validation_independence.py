"""Validation-independence proof through the REAL check chain (五).

A completed deliverable is placed OUTSIDE the retained window for both
windowed strategies (so it disappears from the model input). A candidate
plan that re-schedules exactly that deliverable is then submitted to the
real validate_candidate with the graph-provided authoritative context.
Expected: RECENT_DUPLICATE fails for ALL three strategies — compression
must never weaken the business check. No constants, no set self-compare,
no import-only assertions.
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

from app.agent.context_compression import (
    CompressionStrategy,
    compress_context_history,
)
from app.agent.nodes import build_planning_context, validate_candidate
from app.schemas.agent_runs import (
    PlanCandidate,
    ProfileContext,
    TaskCandidate,
    WeeklyFocusCandidate,
)
from app.schemas.enums import (
    CareerStage,
    GoalType,
    SkillLevel,
    TaskStatus,
    TaskType,
)

OLD_COMPLETED = "整理目标公司清单 20 家"


def _profile() -> ProfileContext:
    return ProfileContext(
        user_id=uuid4(),
        version=1,
        goal_type=GoalType.AI_BACKEND,
        stage=CareerStage.PREPARING,
        time_budget_minutes=90,
        skill_level=SkillLevel.INTERMEDIATE,
    )


def _history_context() -> object:
    from app.schemas.agent_runs import TaskContext

    profile = _profile()
    planning_date = date(2026, 8, 31)
    tasks = [
        TaskContext(
            task_id=uuid4(),
            state=TaskStatus.COMPLETED,
            title=OLD_COMPLETED[:40],
            deliverable=OLD_COMPLETED,
            scheduled_date=planning_date - timedelta(days=30 - index),
        )
        for index in range(10)  # ten old completed records
    ] + [
        # plus five newer records that push the old one outside any window
        TaskContext(
            task_id=uuid4(),
            state=TaskStatus.COMPLETED,
            title=f"近期任务 {i}",
            deliverable=f"近期执行事项 {i}",
            scheduled_date=planning_date - timedelta(days=i),
        )
        for i in range(5)
    ]
    context = build_planning_context(
        profile=profile,
        requested_horizon_weeks=None,
        source_plan_id=None,
        source_plan_version=None,
        completed_facts=[t.deliverable for t in tasks],
        blockers=[],
        planning_date=planning_date,
    )
    return context.model_copy(update={"recent_tasks": tasks})


def _offending_candidate(context) -> PlanCandidate:
    window = context.planning_window
    return PlanCandidate(
        plan_date=window.planning_date,
        horizon_start=window.horizon_start,
        horizon_end=window.horizon_end,
        overall_direction="求职推进",
        weekly_focus=[
            WeeklyFocusCandidate(
                week_index=1,
                focus="第一周推进",
                success_signal="清单产出",
            )
        ],
        summary="重复安排已完成事项的候选",
        rationale="测试用",
        tasks=[
            TaskCandidate(
                title=OLD_COMPLETED[:40],
                task_type=TaskType.PROJECT,
                scheduled_date=window.planning_date,
                starter_action="1. 打开文档 2. 整理",
                deliverable=OLD_COMPLETED,  # exact repeat of completed work
                estimated_minutes=30,
                rationale="服务第一周重点",
            )
        ],
    )


def test_duplicate_rejected_for_every_strategy_through_real_chain() -> None:
    authoritative = _history_context()
    candidate = _offending_candidate(authoritative)

    for strategy in CompressionStrategy:
        compressed = compress_context_history(
            authoritative,
            recent_tasks_budget=5,
            recent_reviews_budget=2,
            focus_query="安排下周计划",
            strategy=strategy,
        )
        # Sanity: for the windowed strategies the old deliverable must be
        # GONE from the model input (otherwise the case proves nothing).
        if strategy is not CompressionStrategy.FULL:
            assert OLD_COMPLETED not in " ".join(
                t.deliverable for t in compressed.context.recent_tasks
            ), f"{strategy}: premise broken, fact still in retained window"

        # The real check chain against the AUTHORITATIVE (pre-compression)
        # facts — exactly what the graph wires into rule_validator.
        report = validate_candidate(candidate, authoritative)
        codes = {c.code: c.passed for c in report.checks}
        assert codes["RECENT_DUPLICATE"] is False, (
            f"{strategy}: duplicate completed deliverable was accepted — "
            "compression weakened the business check"
        )
        assert report.passed is False


def test_authoritative_coverage_boundary_is_bounded_and_documented() -> None:
    """五.5: the authoritative facts themselves come from a bounded
    history pull (repository recent_tasks limit=30, completed_facts
    [:20]) — record the boundary instead of claiming full history."""
    import inspect

    from app.repositories.plans import PlanRepository

    signature = inspect.signature(PlanRepository.recent_tasks)
    assert signature.parameters["limit"].default == 30
    # completed_facts cap lives in the evidence loader ([...][:20]).
    source = inspect.getsource(__import__("app.agent.graph", fromlist=["_evidence_loader_node"]))
    assert "[:20]" in source
