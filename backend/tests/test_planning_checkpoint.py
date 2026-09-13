"""Tests for career_planning_agent node-level checkpoint recovery.

Pins:
* First execution saves a checkpoint; a recovered retry (run requeued to
  pending after a simulated crash) reuses it — the planning provider is
  NOT called again, the run still completes, and the reused step's
  trace carries ``checkpoint_reused: True``.
* A tampered input fingerprint is rejected: the retry re-generates
  through the provider (input immutability is the reuse precondition).
* A corrupt checkpoint payload falls through to fresh generation instead
  of failing the Run.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.agent.executor import AgentRunExecutor
from app.models.agent_run import AgentCheckpoint, AgentEvent, AgentRun, AgentStep
from app.models.plan import Plan, Task
from app.providers.llm import MockPlanningProvider
from tests.test_agent_runtime import create_run, create_user, refresh_run, runtime_factory


class _CountingPlanningProvider(MockPlanningProvider):
    def __init__(self) -> None:
        super().__init__()
        self.generate_calls = 0

    async def generate_plan(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        self.generate_calls += 1
        return await super().generate_plan(*args, **kwargs)

    async def generate_agent_turn(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        self.generate_calls += 1
        return await super().generate_agent_turn(*args, **kwargs)


async def _requeue(db_session: AsyncSession, run_id: UUID) -> None:
    """Simulate recover_interrupted: expired lease → back to pending.

    A genuinely crashed Run never wrote a terminal event (the unique
    index uq_agent_events_one_terminal forbids a second one), so the
    simulation removes it along with the terminal-state columns.
    """

    from sqlalchemy import delete
    from sqlalchemy import select as sa_select

    await db_session.execute(
        delete(AgentEvent).where(
            AgentEvent.run_id == run_id,
            AgentEvent.event_type.in_(
                {"run.completed", "run.degraded", "run.failed", "run.cancelled"}
            ),
        )
    )
    # Detach the final-plan reference first (FK), then rewind the persist
    # side effects: the checkpoint targets a crash BEFORE the persist
    # node, so the retry must find no plan/tasks residue.
    await db_session.execute(
        update(AgentRun)
        .where(AgentRun.id == run_id)
        .values(
            status="pending",
            worker_id=None,
            lease_expires_at=None,
            heartbeat_at=None,
            finished_at=None,
            result_kind=None,
            final_plan_id=None,
        )
    )
    plan_ids = (
        await db_session.scalars(
            sa_select(Plan.id).where(Plan.source_run_id == run_id)
        )
    ).all()
    if plan_ids:
        await db_session.execute(delete(Task).where(Task.plan_id.in_(plan_ids)))
        await db_session.execute(delete(Plan).where(Plan.id.in_(plan_ids)))
    await db_session.flush()


async def _checkpoint(db_session: AsyncSession, run_id: UUID) -> AgentCheckpoint | None:
    return await db_session.scalar(
        select(AgentCheckpoint).where(
            AgentCheckpoint.run_id == run_id,
            AgentCheckpoint.node_name == "career_planning_agent",
        )
    )


@pytest.mark.asyncio
async def test_recovered_retry_reuses_checkpoint_without_recalling_provider(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    user_id = await create_user(db_session)
    run = await create_run(db_session, user_id, key="checkpoint-reuse")
    provider = _CountingPlanningProvider()
    factory = runtime_factory(db_connection)
    executor = AgentRunExecutor(factory, provider=provider)

    await executor.execute(run.id)
    first = await refresh_run(db_session, run.id)
    assert first.status == "completed"
    assert provider.generate_calls == 1

    saved = await _checkpoint(db_session, run.id)
    assert saved is not None

    await _requeue(db_session, run.id)
    await executor.execute(run.id)
    second = await refresh_run(db_session, run.id)
    assert second.status == "completed"
    # The expensive call was NOT repeated: still exactly one generation.
    assert provider.generate_calls == 1
    assert second.attempt_count == 2

    # Steps record the node-local attempt (always 1 here); identify the
    # retry's step as the latest one for this node.
    reused_step = await db_session.scalar(
        select(AgentStep)
        .where(
            AgentStep.run_id == run.id,
            AgentStep.node_name == "career_planning_agent",
        )
        .order_by(AgentStep.sequence.desc())
        .limit(1)
    )
    assert reused_step is not None
    assert reused_step.trace_data.get("checkpoint_reused") is True


@pytest.mark.asyncio
async def test_tampered_fingerprint_is_rejected(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    user_id = await create_user(db_session)
    run = await create_run(db_session, user_id, key="checkpoint-drift")
    provider = _CountingPlanningProvider()
    factory = runtime_factory(db_connection)
    executor = AgentRunExecutor(factory, provider=provider)

    await executor.execute(run.id)
    assert provider.generate_calls == 1

    saved = await _checkpoint(db_session, run.id)
    assert saved is not None
    # Tamper: pretend the checkpoint was written for different inputs.
    tampered = dict(saved.state_json)
    tampered["input_hash"] = "0" * 64
    saved.state_json = tampered
    await db_session.flush()

    await _requeue(db_session, run.id)
    await executor.execute(run.id)
    second = await refresh_run(db_session, run.id)
    assert second.status == "completed"
    # Fingerprint mismatch → fresh generation through the provider.
    assert provider.generate_calls == 2


@pytest.mark.asyncio
async def test_corrupt_checkpoint_falls_through(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    user_id = await create_user(db_session)
    run = await create_run(db_session, user_id, key="checkpoint-corrupt")
    provider = _CountingPlanningProvider()
    factory = runtime_factory(db_connection)
    executor = AgentRunExecutor(factory, provider=provider)

    await executor.execute(run.id)
    saved = await _checkpoint(db_session, run.id)
    assert saved is not None

    # Corrupt the candidate payload while keeping a valid-looking hash.
    corrupted = dict(saved.state_json)
    corrupted["candidate"] = {"not": "a-valid-candidate"}
    encoded = json.dumps(corrupted, sort_keys=True, separators=(",", ":"))
    from hashlib import sha256

    saved.state_json = corrupted
    saved.state_hash = sha256(encoded.encode()).hexdigest()
    await db_session.flush()

    await _requeue(db_session, run.id)
    await executor.execute(run.id)
    second = await refresh_run(db_session, run.id)
    # Corrupt payload → rejected → regenerated → Run still completes.
    assert second.status == "completed"
    assert provider.generate_calls == 2
    _ = datetime.now(UTC) + timedelta(minutes=1)  # keep import symmetry
