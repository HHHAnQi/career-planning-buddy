"""Graph-node validation independence with FULL-rendered-input absence (六).

Chain under test (all real): executor run builds the compressed model
input; the rendered request is reconstructed exactly as the graph would
render it; a duplicate-scheduling candidate is then pushed through the
graph's REAL _validator_node (NodeRunner + step recording + trace) with
the authoritative context. Precondition asserted first: the old
completed deliverable is absent from EVERY part of the rendered input —
retained records, summaries, AND completed_facts.
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.agent.executor import AgentRunExecutor
from app.core.config import get_settings
from app.core.security import TokenService
from app.core.time import product_today
from app.models.agent_run import AgentRun
from app.models.plan import Plan, Task
from app.providers.llm import MockPlanningProvider
from app.schemas.agent_runs import (
    PlanCandidate,
    PlanningContext,
    TaskCandidate,
    TaskContext,
    WeeklyFocusCandidate,
)
from app.schemas.enums import (
    CareerStage,
    GoalType,
    ReplanMode,
    SkillLevel,
    TaskStatus,
    TaskType,
)
from app.schemas.profile import ProfilePutRequest
from app.services.agent_runs import AgentRunService
from app.services.auth import AuthService
from app.services.profiles import ProfileService

TARGET = "最旧的已完成事项：整理 2019 年实习笔记"
PLANNING_DATE = product_today()


class _Noop:
    def submit(self, run_id):
        return None

    async def request_cancel(self, run_id):
        return None


async def _seed_user_with_crowded_history(db_session) -> tuple:
    settings = get_settings()
    auth = AuthService(db_session, TokenService(settings))
    user = (await auth.login_guest(None)).user
    await ProfileService(db_session).put(
        user_id=user.id,
        payload=ProfilePutRequest(
            goal_type=GoalType.AI_BACKEND,
            stage=CareerStage.PREPARING,
            time_budget_minutes=90,
            skill_level=SkillLevel.INTERMEDIATE,
            skill_summary="FastAPI",
            start_date=PLANNING_DATE,
            deadline=PLANNING_DATE + timedelta(days=34),
        ),
        idempotency_key=f"graph-ind-{user.id}",
    )
    anchor = AgentRun(
        id=uuid4(),
        user_id=user.id,
        run_kind="planning",
        idempotency_key=f"graph-ind-anchor-{uuid4()}",
        request_text="历史锚点",
        status="completed",
        graph_version="test",
        config_snapshot_json={"note": "anchor"},
        deadline_at=PLANNING_DATE,
    )
    db_session.add(anchor)
    await db_session.flush()
    plan = Plan(
        id=uuid4(),
        user_id=user.id,
        version=1,
        status="completed",
        plan_date=PLANNING_DATE - timedelta(days=40),
        horizon_start=PLANNING_DATE - timedelta(days=40),
        horizon_end=PLANNING_DATE - timedelta(days=13),
        overall_direction="历史",
        weekly_focus_json=[],
        source_run_id=anchor.id,
    )
    plan.summary = "历史"
    plan.rationale = "历史"
    db_session.add(plan)
    await db_session.flush()

    def _task(deliverable: str, day_offset: int, completed: bool = True) -> Task:
        return Task(
            id=uuid4(),
            user_id=user.id,
            plan_id=plan.id,
            order_index=abs(day_offset),
            title=deliverable[:40],
            task_type="other",
            state="completed" if completed else "abandoned",
            scheduled_date=PLANNING_DATE - timedelta(days=day_offset),
            deliverable=deliverable,
            starter_action="1. 历史",
            estimated_minutes=30,
            actual_minutes=30 if completed else None,
            abandoned_reason=None if completed else "other",
            abandoned_reason_text=None if completed else "原因",
            # Distinct update timestamps (newest work has the newest
            # updated_at) make the repository's updated_at-desc ordering
            # deterministic, so the [:5] caps consistently exclude the
            # oldest rows.
            updated_at=date.today() - timedelta(days=abs(day_offset)),
        )

    # 7 OLD completed (TARGET is the oldest, 40 days back); 6 NEWER
    # completed crowd every [:5] cap (window, summary, completed_facts).
    rows = [_task(TARGET, 40)]
    rows += [_task(f"旧批次事项 {i}", 35 - i) for i in range(6)]
    rows += [_task(f"近期事项 {i}", 6 - i) for i in range(6)]
    for row in rows:
        db_session.add(row)
    return user.id, plan.id


@pytest.mark.asyncio
async def test_validator_node_rejects_duplicate_absent_from_all_render(
    db_connection, db_session, monkeypatch
) -> None:
    import tests.test_agent_runtime as rt

    user_id, _ = await _seed_user_with_crowded_history(db_session)
    service = AgentRunService(db_session, get_settings(), _Noop())
    run = await service.create(
        user_id=user_id,
        message="安排下周计划",
        hint_intent="create_plan",
        goal_type_override=None,
        source_plan_id=None,
        idempotency_key=f"graph-ind-run-{uuid4()}",
    )
    await db_session.commit()

    from app.core.config import get_settings as _gs

    _gs.cache_clear()
    import os

    os.environ["LLM_PROVIDER"] = "mock"
    _gs.cache_clear()
    try:
        await AgentRunExecutor(rt.runtime_factory(db_connection)).execute(run.id)
    finally:
        os.environ.pop("LLM_PROVIDER", None)
        _gs.cache_clear()

    completed = (
        await db_session.execute(
            select(AgentRun)
            .where(AgentRun.id == run.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert completed.status in {"completed", "degraded"}

    # ---- rebuild the EXACT compressed context the graph rendered ----
    from app.schemas.agent_runs import RunInputSnapshot

    snap = RunInputSnapshot.model_validate(completed.input_snapshot_json)
    # The persisted snapshot stores LOADER facts (pre-compression), so
    # re-running the SAME compression algorithm on the rebuilt full
    # history (frozen strategy + budgets from the run's config snapshot)
    # reproduces the rendered context exactly.
    from app.agent.context_compression import compress_context_history

    all_tasks = (
        (
            await db_session.execute(
                select(Task)
                .where(Task.user_id == user_id)
                .order_by(Task.scheduled_date.desc())
            )
        )
        .scalars()
        .all()
    )
    loader_facts = [
        t.deliverable for t in all_tasks if t.state == "completed"
    ][:20]
    full_context = PlanningContext(
        profile=snap.profile,
        planning_window=snap.planning_window,
        recent_tasks=[
            TaskContext(
                task_id=t.id,
                state=TaskStatus(t.state),
                title=t.title,
                deliverable=t.deliverable,
                scheduled_date=t.scheduled_date,
                abandoned_reason_text=t.abandoned_reason_text,
            )
            for t in all_tasks
        ],
        completed_facts=loader_facts,
        time_budget_minutes=snap.time_budget_minutes,
        token_estimate=0,
    )
    frozen_cfg = dict(completed.config_snapshot_json or {})
    compression = compress_context_history(
        full_context,
        recent_tasks_budget=frozen_cfg.get("context_recent_tasks_budget", 5),
        recent_reviews_budget=frozen_cfg.get("context_recent_reviews_budget", 2),
        focus_query="安排下周计划",
        strategy=frozen_cfg.get(
            "context_compression_strategy", "relevant_summary"
        ),
    )
    compressed = compression.context
    from app.prompts.career_planning import generation_messages

    rendered = generation_messages(
        message="安排下周计划",
        context=compressed,
        replan_mode=ReplanMode.INITIAL,
    )
    full_text = "".join(m["content"] for m in rendered)

    # Precondition 1: TARGET absent from EVERY rendered part — records,
    # summary, completed_facts — i.e. the model never sees it.
    assert TARGET not in full_text, (
        "precondition broken: target still present in rendered input"
    )

    # Precondition 2: the authoritative fact set still contains it.
    authoritative = compressed.model_copy(
        update={"completed_facts": [*snap.completed_facts, TARGET]}
    )
    assert TARGET in authoritative.completed_facts

    # ---- push a duplicate-scheduling candidate through the REAL node ----
    window = snap.planning_window
    duplicate = PlanCandidate(
        plan_date=window.planning_date,
        horizon_start=window.horizon_start,
        horizon_end=window.horizon_end,
        overall_direction="重复旧事项",
        weekly_focus=[
            WeeklyFocusCandidate(week_index=1, focus="推进", success_signal="产出")
        ],
        summary="重复候选",
        rationale="测试",
        tasks=[
            TaskCandidate(
                title=TARGET[:40],
                task_type=TaskType.OTHER,
                scheduled_date=window.planning_date,
                starter_action="1. 打开 2. 整理",
                deliverable=TARGET,
                estimated_minutes=30,
                rationale="服务第一周",
            )
        ],
    )

    # A fresh pending Run hosts the validator step (event sequencing
    # forbids appending steps to a terminal Run).
    validator_run = await service.create(
        user_id=user_id,
        message="校验宿主",
        hint_intent=None,
        goal_type_override=None,
        source_plan_id=None,
        idempotency_key=f"graph-ind-host-{uuid4()}",
    )
    await db_session.commit()

    # Build through the factory the runtime uses (NodeRunner + Budget):
    from datetime import UTC, datetime

    from app.agent.finalizer import AgentRunFinalizer
    from app.agent.graph import GraphFactory
    from app.agent.node_runner import NodeRunner
    from app.harness.budget import BudgetGuard, CancellationToken
    from app.harness.snapshots import SnapshotService

    config = SnapshotService.build_config(get_settings())
    budget = BudgetGuard(
        config, datetime.now(UTC) + timedelta(seconds=60), CancellationToken()
    )
    node_runner = NodeRunner(
        rt.runtime_factory(db_connection),
        budget,
        config.node_timeouts_seconds,
    )
    finalizer = AgentRunFinalizer(
        rt.runtime_factory(db_connection), budget, worker_id="test"
    )
    real_graph = GraphFactory(
        rt.runtime_factory(db_connection), MockPlanningProvider()
    ).build(node_runner=node_runner, finalizer=finalizer, budget=budget)

    state = {
        "run_id": validator_run.id,
        "candidate_plan": duplicate,
        "planning_context": compressed,
        "authoritative_context": authoritative,
        "candidate_evidence_visibility": None,
        "validation_attempt": 0,
    }
    result = await real_graph._validator_node(state)  # noqa: SLF001
    await db_session.rollback()
    from app.models.agent_run import AgentStep

    steps = list(
        await db_session.scalars(
            select(AgentStep).where(AgentStep.run_id == validator_run.id)
        )
    )
    assert any(s.node_name == "rule_validator" for s in steps)
    report = result["validation_report"]
    codes = {c.code: c.passed for c in report.checks}
    assert codes["RECENT_DUPLICATE"] is False, (
        "duplicate of a completed item absent from model input was "
        "accepted by the real validator node"
    )
    assert report.passed is False
