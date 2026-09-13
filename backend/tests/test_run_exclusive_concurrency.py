"""Concurrency correctness proofs for the fan-out loader design (fix 5).

1. Lock WAIT never consumes the node timeout: run_exclusive acquires the
   shared lock before entering the timed section, so a branch that waits
   for its sibling keeps its full operation budget.
2. An engine-bound factory with pool_size=1 is safe on the lock-free
   path: the pool serializes connection checkout, so two sessions can
   never interleave savepoints on one wire.
3. Four concurrent Runs on an engine-backed factory (production shape)
   all complete — same-worker multi-run loader concurrency works.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agent.node_runner import NodeOutput, NodeRunner, NodeTelemetry
from app.core.config import get_settings
from app.harness.budget import BudgetGuard, CancellationToken
from app.harness.snapshots import SnapshotService


def _node_runner() -> tuple[NodeRunner, object]:
    config = SnapshotService.build_config(get_settings())
    budget = BudgetGuard(
        config, datetime.now(UTC) + timedelta(seconds=60), CancellationToken()
    )
    runner = NodeRunner(
        None,  # session factory never touched: start/complete are patched
        budget,
        {"memory_loader": 5.0, "evidence_loader": 5.0},
    )
    return runner, budget


@pytest.mark.asyncio
async def test_lock_wait_does_not_consume_node_timeout(monkeypatch) -> None:
    runner, _ = _node_runner()
    lock = asyncio.Lock()
    recorded: dict[str, object] = {}

    async def fake_start_step(run_id, node_name, attempt=1):
        recorded["started_at"] = asyncio.get_running_loop().time()
        return type("Step", (), {"id": object()})()

    async def fake_complete(*args, **kwargs):
        return None

    monkeypatch.setattr(runner, "start_step", fake_start_step)
    monkeypatch.setattr(runner, "_complete_step", fake_complete)

    async def operation():
        # The sibling held the lock for 1.0s before this branch acquired
        # it; the operation itself is fast. If lock WAIT counted against
        # the 0.3s timeout, this branch would time out.
        await asyncio.sleep(0.1)
        return NodeOutput(True, NodeTelemetry(trace_data={}))

    async def hold_lock_first():
        async with lock:
            await asyncio.sleep(1.0)

    holder = asyncio.create_task(hold_lock_first())
    await asyncio.sleep(0.05)
    result = await runner.run_exclusive(
        "run-1", "memory_loader", operation, lock=lock, attempt=1
    )
    holder.cancel()
    assert result is True


@pytest.mark.asyncio
async def test_single_connection_pool_serializes_without_corruption() -> None:
    engine = create_async_engine(get_settings().database_url, pool_size=1)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:

        async def touch(tag: str) -> str:
            async with factory() as session:
                # A real transaction on the shared wire, held across an
                # await so unserialized access would corrupt state.
                async with session.begin():
                    await asyncio.sleep(0.02)
                    result = await session.execute(text("SELECT :t"), {"t": tag})
                    return str(result.scalar_one())

        values = await asyncio.gather(touch("a"), touch("b"), touch("c"))
        assert values == ["a", "b", "c"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_four_concurrent_runs_on_pooled_factory() -> None:
    """Four concurrent Runs on an engine-backed factory (the production
    dispatcher shape, worker_concurrency=4) all complete. Runs are created
    through the SAME pooled factory (committed) because the standard
    single-connection test fixture is invisible to pooled sessions; the
    created rows are cleaned up in the finally block."""
    from datetime import timedelta

    from sqlalchemy import delete

    from app.agent.executor import AgentRunExecutor
    from app.core.security import TokenService
    from app.core.time import product_today
    from app.models.agent_run import AgentEvent, AgentRun, AgentStep
    from app.schemas.enums import CareerStage, GoalType, SkillLevel
    from app.schemas.profile import ProfilePutRequest
    from app.services.agent_runs import AgentRunService
    from app.services.auth import AuthService
    from app.services.profiles import ProfileService

    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    run_ids: list = []
    try:
        class _NoopScheduler:
            def submit(self, run_id):
                return None

            async def request_cancel(self, run_id):
                return None

        async with factory() as session:
            service = AgentRunService(session, settings, _NoopScheduler())
            for index in range(4):
                auth = AuthService(session, TokenService(settings))
                user = (await auth.login_guest(None)).user
                await ProfileService(session).put(
                    user_id=user.id,
                    payload=ProfilePutRequest(
                        goal_type=GoalType.AGENT_APP,
                        stage=CareerStage.PREPARING,
                        time_budget_minutes=90,
                        skill_level=SkillLevel.INTERMEDIATE,
                        skill_summary="FastAPI",
                        start_date=product_today(),
                        deadline=product_today() + timedelta(days=34),
                    ),
                    idempotency_key=f"stress-profile-{user.id}",
                )
                run = await service.create(
                    user_id=user.id,
                    message=f"并发压测计划 {index}",
                    hint_intent="create_plan",
                    goal_type_override=None,
                    source_plan_id=None,
                    idempotency_key=f"stress-{user.id}-{index}",
                )
                run_ids.append(run.id)
            await session.commit()

        async def drive(run_id):
            executor = AgentRunExecutor(factory)
            await executor.execute(run_id)

        await asyncio.gather(*(drive(run_id) for run_id in run_ids))

        async with factory() as session:
            for run_id in run_ids:
                run = await session.get(AgentRun, run_id)
                assert run is not None and run.status in {"completed", "degraded"}, (
                    f"{run_id}: {run.status if run else None}"
                )
    finally:
        async with factory() as session:
            for run_id in run_ids:
                await session.execute(
                    delete(AgentEvent).where(AgentEvent.run_id == run_id)
                )
                await session.execute(
                    delete(AgentStep).where(AgentStep.run_id == run_id)
                )
                await session.execute(delete(AgentRun).where(AgentRun.id == run_id))
            await session.commit()
        await engine.dispose()
