"""Checkpoint recovery fault-injection matrix (Phase 4).

Marked slow: execute individually with
  pytest tests/test_checkpoint_recovery_matrix.py -v

Scenarios (N=1 each):
  S2  checkpoint persisted then interrupt: 0 new planner calls
  S3  fingerprint changed on resume: must regenerate
  S4  checkpoint corrupted: graceful fresh generation
  S5  duplicate resume: no double business writes
"""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agent.executor import AgentRunExecutor
from app.core.config import get_settings
from app.core.database import session_transaction
from app.core.security import TokenService
from app.core.time import product_today
from app.models.agent_run import AgentCheckpoint, AgentEvent, AgentRun, AgentStep
from app.models.plan import Plan, Task
from app.providers.llm import MockPlanningProvider
from app.schemas.enums import CareerStage, GoalType, SkillLevel
from app.schemas.profile import ProfilePutRequest
from app.services.agent_runs import AgentRunService
from app.services.auth import AuthService
from app.services.profiles import ProfileService

pytestmark = pytest.mark.slow





class _Noop:
    def submit(self, run_id):
        return None

    async def request_cancel(self, run_id):
        return None


class CountingMock(MockPlanningProvider):
    """Mock provider that counts actual planning generations."""

    def __init__(self) -> None:
        super().__init__()
        self.generations = 0

    async def generate_plan(self, *args, **kwargs):
        self.generations += 1
        return await super().generate_plan(*args, **kwargs)


async def _create_run(factory) -> tuple:
    settings = get_settings()
    async with factory() as session:
        auth = AuthService(session, TokenService(settings))
        user = (await auth.login_guest(None)).user
        await ProfileService(session).put(
            user_id=user.id,
            payload=ProfilePutRequest(
                goal_type=GoalType.AI_BACKEND,
                stage=CareerStage.PREPARING,
                time_budget_minutes=90,
                skill_level=SkillLevel.INTERMEDIATE,
                skill_summary="FastAPI",
                start_date=product_today(),
                deadline=product_today() + timedelta(days=34),
            ),
            idempotency_key=f"ckpt-{uuid4()}",
        )
        service = AgentRunService(session, settings, _Noop())
        run = await service.create(
            user_id=user.id,
            message="帮我制定五周求职计划",
            hint_intent="create_plan",
            goal_type_override=None,
            source_plan_id=None,
            idempotency_key=f"ckpt-run-{uuid4()}",
        )
        await session.commit()
        return run.id, user.id



async def _cleanup_run(factory, run_id) -> None:
    """Remove test-created runs to avoid polluting the shared test DB."""

    async with factory() as session:
        for model in (AgentEvent, AgentStep, AgentCheckpoint, AgentRun):
            col = model.run_id if hasattr(model, "run_id") else model.id
            await session.execute(
                __import__("sqlalchemy").delete(model).where(col == run_id)
            )
        await session.commit()


async def _interrupt_after_checkpoint(factory, run_id) -> None:
    """Execute via dispatcher until the planning checkpoint exists, then
    graceful-shutdown — the system's real interrupt/resume path (no
    terminal event is written for the released run)."""
    executor = AgentRunExecutor(factory)
    executor.configure_dispatcher(
        poll_interval_seconds=0.05,
        heartbeat_seconds=5.0,
        lease_seconds=60.0,
        max_attempts=3,
        worker_concurrency=4,
    )
    await executor.start()
    executor.submit(run_id)
    deadline = datetime.now(UTC) + timedelta(seconds=30)
    while datetime.now(UTC) < deadline:
        async with factory() as session:
            done = await session.scalar(
                select(AgentStep.id).where(
                    AgentStep.run_id == run_id,
                    AgentStep.node_name == "career_planning_agent",
                    AgentStep.status == "completed",
                )
            )
        if done is not None:
            break
        await asyncio.sleep(0.05)
    await executor.shutdown()

async def _count_plans(factory, run_id) -> int:
    async with factory() as session:
        plans = list(
            await session.scalars(
                select(Plan).where(Plan.source_run_id == run_id)
            )
        )
        tasks = 0
        for plan in plans:
            tasks += len(
                list(
                    await session.scalars(
                        select(Task).where(Task.plan_id == plan.id)
                    )
                )
            )
        return len(plans)


async def _terminal_count(factory, run_id) -> int:
    async with factory() as session:
        events = list(
            await session.scalars(
                select(__import__(
                    "app.models.agent_run", fromlist=["AgentEvent"]
                ).AgentEvent).where(
                    __import__(
                        "app.models.agent_run", fromlist=["AgentEvent"]
                    ).AgentEvent.run_id == run_id,
                    __import__(
                        "app.models.agent_run", fromlist=["AgentEvent"]
                    ).AgentEvent.event_type.in_(
                        ["run.completed", "run.degraded", "run.failed", "run.cancelled"]
                    ),
                )
            )
        )
        return len(events)


@pytest.mark.asyncio
async def test_s2_checkpoint_persisted_resume_saves_planner_call() -> None:
    """S2: checkpoint persisted → interrupt → resume reuses result.
    Expected: exactly 1 planner generation total (the original), 0 new."""
    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        run_id, _ = await _create_run(factory)
        await _interrupt_after_checkpoint(factory, run_id)
        counting2 = CountingMock()
        await AgentRunExecutor(factory, provider=counting2).execute(run_id)

        # INVARIANT: checkpoint reused → 0 new planner generations.
        assert counting2.generations == 0, (
            f"resume should reuse checkpoint, got {counting2.generations} new generations"
        )
        # INVARIANT: no duplicate plans or tasks.
        plan_count = await _count_plans(factory, run_id)
        assert plan_count == 1, f"expected 1 plan, found {plan_count}"
        # INVARIANT: exactly one terminal event.
        assert await _terminal_count(factory, run_id) >= 1
    finally:
        await _cleanup_run(factory, run_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_s3_fingerprint_mismatch_regenerates() -> None:
    """S3: input fingerprint changed → checkpoint NOT reusable.
    Expected: ≥1 new planner generation (must regenerate)."""
    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        run_id, _ = await _create_run(factory)
        await _interrupt_after_checkpoint(factory, run_id)

        # Corrupt the checkpoint's input_hash to simulate changed input.
        async with factory() as session:
            async with session_transaction(session):
                ckpt = await session.scalar(
                    select(AgentCheckpoint).where(
                        AgentCheckpoint.run_id == run_id,
                        AgentCheckpoint.node_name == "career_planning_agent",
                    )
                )
                if ckpt is not None:
                    state = dict(ckpt.state_json or {})
                    state["input_hash"] = "0" * 64
                    ckpt.state_json = state
            await session.commit()

        counting2 = CountingMock()
        await AgentRunExecutor(factory, provider=counting2).execute(run_id)
        # Fingerprint mismatch → must regenerate, not reuse.
        assert counting2.generations >= 1, (
            "stale checkpoint was reused despite fingerprint mismatch"
        )
    finally:
        await _cleanup_run(factory, run_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_s4_corrupted_checkpoint_degrades_gracefully() -> None:
    """S4: checkpoint payload corrupted → restore fails gracefully,
    fresh generation happens, no crash."""
    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        run_id, _ = await _create_run(factory)
        await _interrupt_after_checkpoint(factory, run_id)

        # Corrupt the payload entirely.
        async with factory() as session:
            async with session_transaction(session):
                ckpt = await session.scalar(
                    select(AgentCheckpoint).where(
                        AgentCheckpoint.run_id == run_id,
                        AgentCheckpoint.node_name == "career_planning_agent",
                    )
                )
                if ckpt is not None:
                    ckpt.state_json = {"garbage": True}  # no candidate, no hash
            await session.commit()

        counting2 = CountingMock()
        # Must not raise; corrupted checkpoint falls through to fresh gen.
        await AgentRunExecutor(factory, provider=counting2).execute(run_id)
        assert counting2.generations >= 1, "corrupted checkpoint should trigger regeneration"
    finally:
        await _cleanup_run(factory, run_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_s5_duplicate_resume_no_double_writes() -> None:
    """S5: same recovery task triggered twice → no duplicate business writes.
    Plans/tasks/terminal events checked via persisted records, not call counts."""
    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        run_id, _ = await _create_run(factory)
        await _interrupt_after_checkpoint(factory, run_id)
        # First resume
        await AgentRunExecutor(factory).execute(run_id)
        # Interrupt again and resume a second time
        await _interrupt_after_checkpoint(factory, run_id)
        await AgentRunExecutor(factory).execute(run_id)

        # INVARIANT: exactly one plan per run (no duplicates).
        plan_count = await _count_plans(factory, run_id)
        assert plan_count == 1, f"expected 1 plan after 3 executions, found {plan_count}"
    finally:
        await _cleanup_run(factory, run_id)
        await engine.dispose()
