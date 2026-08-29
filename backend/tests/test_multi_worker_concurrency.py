"""True-concurrency claim tests for horizontal-scaling correctness.

Unlike ``test_database_lease_allows_only_one_worker_to_claim_a_run``
(which claims sequentially), these tests race claimants concurrently
through an engine-backed session factory — the same connection-pool
topology as two backend replicas.

Pins:
* Two workers calling ``_claim_by_id`` for the same pending Run at the
  same instant: exactly one wins (``FOR UPDATE`` + status check under
  the row lock), attempt_count increments exactly once, the loser gets
  None.
* Two workers draining a queue of pending Runs via ``_claim_next``
  (``FOR UPDATE SKIP LOCKED``) concurrently: every Run is claimed by
  exactly one worker, no run is claimed twice, no run is lost.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.agent.executor import AgentRunExecutor
from app.core.config import get_settings
from app.core.database import session_transaction
from app.core.security import TokenService
from app.models.agent_run import AgentRun
from app.services.auth import AuthService


async def _provision(
    factory: async_sessionmaker[AsyncSession], *, count: int, prefix: str
) -> list[AgentRun]:
    async with factory() as session:
        async with session_transaction(session):
            # One user per Run: uq_agent_runs_one_active_per_user forbids
            # multiple pending Runs for the same user.
            auth = AuthService(session, TokenService(get_settings()))
            now = datetime.now(UTC)
            runs = []
            for index in range(count):
                user = (await auth.login_guest(None)).user
                runs.append(
                    AgentRun(
                        user_id=user.id,
                        idempotency_key=f"{prefix}-{user.id}-{index}",
                        request_text="Create a plan",
                        resolved_intent="create_plan",
                        replan_mode="initial",
                        status="pending",
                        graph_version="concurrency-test-v1",
                        config_snapshot_json={
                            "provider": "mock",
                            "model_alias": "mock-career-planner-v1",
                            "feature_stage": 6,
                            "graph_version": "concurrency-test-v1",
                            "prompt_versions": {
                                "career_planning": "mock_plan_stage6_context_v1",
                                "format_repair": "mock_format_repair_v1",
                                "business_repair": "mock_business_repair_v1",
                            },
                        },
                        deadline_at=now + timedelta(minutes=10),
                    )
                )
            session.add_all(runs)
            await session.flush()
            return list(runs)


async def _terminate_runs(
    factory: async_sessionmaker[AsyncSession], run_ids: set[Any]
) -> None:
    """Leave no `running` rows behind: startup-recovery tests count them."""

    from sqlalchemy import update

    async with factory() as session:
        async with session_transaction(session):
            await session.execute(
                update(AgentRun)
                .where(AgentRun.id.in_(run_ids))
                .values(status="cancelled", error_code="TEST_CLEANUP")
            )


@pytest.fixture
async def engine_factory() -> Any:
    engine = create_async_engine(get_settings().database_url, pool_size=8)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_claim_by_id_has_exactly_one_winner(
    engine_factory: Any,
) -> None:
    (run,) = await _provision(engine_factory, count=1, prefix="race-by-id")
    first = AgentRunExecutor(engine_factory)
    second = AgentRunExecutor(engine_factory)

    claimed_first, claimed_second = await asyncio.gather(
        first._claim_by_id(run.id),  # noqa: SLF001 - race under test
        second._claim_by_id(run.id),  # noqa: SLF001
    )
    winners = [result for result in (claimed_first, claimed_second) if result is not None]
    assert len(winners) == 1, "both workers claimed the same Run"

    async with engine_factory() as session:
        async with session_transaction(session):
            row = await session.scalar(select(AgentRun).where(AgentRun.id == run.id))
            assert row is not None
            assert row.status == "running"
            # Exactly one claim → exactly one attempt increment.
            assert row.attempt_count == 1
            assert row.worker_id is not None
    await _terminate_runs(engine_factory, {run.id})


@pytest.mark.asyncio
async def test_concurrent_queue_drain_claims_every_run_exactly_once(
    engine_factory: Any,
) -> None:
    runs = await _provision(engine_factory, count=6, prefix="race-queue")
    run_ids = {run.id for run in runs}
    workers = [AgentRunExecutor(engine_factory) for _ in range(2)]

    async def drain(worker: AgentRunExecutor) -> list[Any]:
        claimed: list[Any] = []
        while True:
            loaded = await worker._claim_next()  # noqa: SLF001 - race under test
            if loaded is None:
                break
            claimed.append(loaded)
            await asyncio.sleep(0)  # interleave the two drainers
        return claimed

    first_batch, second_batch = await asyncio.gather(drain(workers[0]), drain(workers[1]))
    all_claimed = [run for run, _config in first_batch + second_batch]
    claimed_ids = [run.id for run in all_claimed]

    # No run claimed twice, none lost, no foreign run picked up.
    assert len(claimed_ids) == len(set(claimed_ids)), "a Run was claimed twice"
    assert set(claimed_ids) == run_ids, "queue drain lost or invented Runs"

    async with engine_factory() as session:
        async with session_transaction(session):
            rows = (
                await session.scalars(select(AgentRun).where(AgentRun.id.in_(run_ids)))
            ).all()
            assert all(row.status == "running" for row in rows)
            assert all(row.attempt_count == 1 for row in rows)
            assert len({row.worker_id for row in rows}) == 2, (
                "expected both workers to participate in the drain"
            )
    await _terminate_runs(engine_factory, run_ids)
