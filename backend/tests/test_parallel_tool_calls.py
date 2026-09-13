"""Tests for parallel tool execution inside one planning round.

Uses an engine-backed session factory (pool of connections), matching
production topology: each concurrent tool execution opens its own
session. The shared-connection test factory cannot represent true
concurrency (savepoints on one connection serialize and corrupt).

Pins:
* Tools accepted in the same round run concurrently — two handlers that
  each sleep 150 ms complete in well under 2×150 ms of wall time.
* Submission order is preserved regardless of completion order (the
  slow-first pair still yields results in call order).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.core.database import session_transaction
from app.models.agent_run import AgentRun, AgentStep
from app.tools.contracts import ModelToolSpec, RegisteredTool, ToolContext
from app.tools.registry import ToolRegistry


class _SlowInput(BaseModel):
    delay_seconds: float = 0.15


class _SlowOutput(BaseModel):
    marker: str


class _SlowHandler:
    def __init__(self, marker: str, delay: float) -> None:
        self._marker = marker
        self._delay = delay

    async def __call__(self, payload: BaseModel, context: ToolContext) -> BaseModel:
        request = _SlowInput.model_validate(payload)
        await asyncio.sleep(request.delay_seconds)
        return _SlowOutput(marker=self._marker)


def _register(registry: ToolRegistry, name: str, marker: str, delay: float) -> None:
    registry.register(
        RegisteredTool(
            spec=ModelToolSpec(
                name=name,
                description="slow test tool",
                input_json_schema={"type": "object"},
                contract_version="1.0",
            ),
            input_model=_SlowInput,
            output_model=_SlowOutput,
            handler=_SlowHandler(marker, delay),
            timeout_seconds=5.0,
        )
    )


async def _provision_run(
    factory: async_sessionmaker[AsyncSession],
) -> tuple[UUID, UUID, UUID]:
    """Create a guest user and a running Run through the same engine."""

    from app.core.security import TokenService
    from app.services.auth import AuthService

    async with factory() as session:
        async with session_transaction(session):
            user = (
                await AuthService(session, TokenService(get_settings())).login_guest(None)
            ).user
            now = datetime.now(UTC)
            run = AgentRun(
                user_id=user.id,
                idempotency_key=f"parallel-tools-{user.id}",
                request_text="Create a plan",
                resolved_intent="create_plan",
                replan_mode="initial",
                status="running",
                graph_version="parallel-test-v1",
                config_snapshot_json={"provider": "mock"},
                deadline_at=now + timedelta(minutes=5),
                worker_id="parallel-test",
                lease_expires_at=now + timedelta(minutes=5),
                heartbeat_at=now,
            )
            session.add(run)
            await session.flush()
            step = AgentStep(
                run_id=run.id,
                sequence=1,
                node_name="career_planning_agent",
                attempt=1,
                status="running",
                trace_data={},
            )
            session.add(step)
            await session.flush()
            return run.id, step.id, user.id


def _context(run_id: UUID, user_id: UUID) -> ToolContext:
    return ToolContext(
        run_id=run_id,
        user_id=user_id,
        goal_type="agent_app",
        intent="create_plan",
        requires_fresh_information=False,
        remaining_deadline_ms=10_000,
    )


async def _terminate_run(factory: async_sessionmaker[AsyncSession], run_id: UUID) -> None:
    """Leave no `running` rows behind: startup-recovery tests count them."""

    from sqlalchemy import update

    async with factory() as session:
        async with session_transaction(session):
            await session.execute(
                update(AgentRun)
                .where(AgentRun.id == run_id)
                .values(status="cancelled", error_code="TEST_CLEANUP")
            )


@pytest.fixture
async def engine_factory() -> Any:
    engine = create_async_engine(get_settings().database_url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_same_round_tools_run_concurrently(engine_factory: Any) -> None:
    run_id, step_id, user_id = await _provision_run(engine_factory)
    registry = ToolRegistry(feature_stage=6, session_factory=engine_factory)
    _register(registry, "tool_a", "A", 0.15)
    _register(registry, "tool_b", "B", 0.15)
    context = _context(run_id, user_id)

    started = monotonic()
    results = await asyncio.gather(
        registry.execute(
            tool_name="tool_a",
            arguments={"delay_seconds": 0.15},
            context=context,
            step_id=step_id,
            round_number=1,
        ),
        registry.execute(
            tool_name="tool_b",
            arguments={"delay_seconds": 0.15},
            context=context,
            step_id=step_id,
            round_number=1,
        ),
    )
    elapsed = monotonic() - started
    assert all(result.success for result in results)
    # Serial execution would take >= 0.30s; concurrent must stay below.
    assert elapsed < 0.28, f"tools appear to run serially ({elapsed:.3f}s)"
    await _terminate_run(engine_factory, run_id)


@pytest.mark.asyncio
async def test_submission_order_preserved(engine_factory: Any) -> None:
    run_id, step_id, user_id = await _provision_run(engine_factory)
    registry = ToolRegistry(feature_stage=6, session_factory=engine_factory)
    _register(registry, "slow_a", "A", 0.12)
    _register(registry, "fast_b", "B", 0.01)
    context = _context(run_id, user_id)

    results = await asyncio.gather(
        registry.execute(
            tool_name="slow_a",
            arguments={},
            context=context,
            step_id=step_id,
            round_number=1,
        ),
        registry.execute(
            tool_name="fast_b",
            arguments={},
            context=context,
            step_id=step_id,
            round_number=1,
        ),
    )
    # The fast tool finished first, but gather returns submission order:
    # the first result is still the slow tool's.
    assert results[0].result.data["marker"] == "A"
    assert results[1].result.data["marker"] == "B"
    await _terminate_run(engine_factory, run_id)
