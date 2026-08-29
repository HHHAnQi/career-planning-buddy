"""Uniform persisted execution wrapper for every controlled graph node."""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager as AsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from time import monotonic
from typing import TypeVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.errors import (
    AgentDeadlineExceededError,
    AgentError,
    AgentLeaseLostError,
    RunCancelledError,
)
from app.core.database import session_transaction
from app.harness.budget import BudgetGuard
from app.harness.events import EventRecorder
from app.harness.trace import TraceRecorder
from app.models.agent_run import AgentRun, AgentStep

T = TypeVar("T")


@dataclass(slots=True)
class NodeTelemetry:
    trace_data: dict[str, object] = field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_cny: Decimal = Decimal("0")
    model_id: str | None = None
    prompt_version: str | None = None


@dataclass(slots=True)
class NodeOutput[T]:
    value: T
    telemetry: NodeTelemetry = field(default_factory=NodeTelemetry)


class NodeRunner:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        budget: BudgetGuard,
        node_timeouts: dict[str, float],
        *,
        worker_id: str | None = None,
        attempt_count: int | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._budget = budget
        self._node_timeouts = node_timeouts
        self._worker_id = worker_id
        self._attempt_count = attempt_count

    async def run(
        self,
        run_id: UUID,
        node_name: str,
        operation: Callable[[], Awaitable[NodeOutput[T]]],
        *,
        attempt: int = 1,
    ) -> T:
        self._budget.check()
        step = await self.start_step(run_id, node_name, attempt)
        started = monotonic()
        try:
            timeout = min(
                self._node_timeouts[node_name],
                self._budget.remaining_seconds(),
            )
            output = await asyncio.wait_for(operation(), timeout=timeout)
            self._budget.check()
        except TimeoutError as exc:
            await self.fail_step(
                run_id,
                step.id,
                latency_ms=int((monotonic() - started) * 1000),
                error_code="NODE_TIMEOUT",
                error_message=f"{node_name} exceeded its timeout",
            )
            raise AgentDeadlineExceededError from exc
        except BaseException as exc:
            code = self._error_code(exc)
            error_message = str(exc) if isinstance(exc, AgentError) else type(exc).__name__
            await self.fail_step(
                run_id,
                step.id,
                latency_ms=int((monotonic() - started) * 1000),
                error_code=code,
                error_message=error_message,
            )
            raise
        traced_latency = output.telemetry.trace_data.get("latency_ms")
        latency_ms = max(
            int((monotonic() - started) * 1000),
            traced_latency if isinstance(traced_latency, int) else 0,
        )
        try:
            await self._complete_step(
                run_id,
                step.id,
                latency_ms=latency_ms,
                telemetry=output.telemetry,
            )
        except BaseException as exc:
            await self.fail_step(
                run_id,
                step.id,
                latency_ms=latency_ms,
                error_code=self._error_code(exc),
                error_message=(
                    str(exc) if isinstance(exc, AgentError) else type(exc).__name__
                ),
            )
            raise
        return output.value

    async def run_exclusive(
        self,
        run_id: UUID,
        node_name: str,
        operation: Callable[[], Awaitable[NodeOutput[T]]],
        *,
        lock: "AsyncContextManager[None]",
        attempt: int = 1,
    ) -> T:
        """Run a parallel-superstep node with ALL session work — step
        bookkeeping included — inside ONE continuous lock section, so two
        branches sharing the lock can never interleave transactions on a
        single-connection runtime. Pooled deployments lose nothing: the
        lock is only contended for the duration of DB reads.
        """
        self._budget.check()
        async with lock:
            step = await self.start_step(run_id, node_name, attempt)
            started = monotonic()
            try:
                timeout = min(
                    self._node_timeouts[node_name],
                    self._budget.remaining_seconds(),
                )
                output = await asyncio.wait_for(operation(), timeout=timeout)
                self._budget.check()
            except TimeoutError as exc:
                await self.fail_step(
                    run_id,
                    step.id,
                    latency_ms=int((monotonic() - started) * 1000),
                    error_code="NODE_TIMEOUT",
                    error_message=f"{node_name} exceeded its timeout",
                )
                raise AgentDeadlineExceededError from exc
            except BaseException as exc:
                code = self._error_code(exc)
                error_message = (
                    str(exc) if isinstance(exc, AgentError) else type(exc).__name__
                )
                await self.fail_step(
                    run_id,
                    step.id,
                    latency_ms=int((monotonic() - started) * 1000),
                    error_code=code,
                    error_message=error_message,
                )
                raise
            traced_latency = output.telemetry.trace_data.get("latency_ms")
            latency_ms = max(
                int((monotonic() - started) * 1000),
                traced_latency if isinstance(traced_latency, int) else 0,
            )
            try:
                await self._complete_step(
                    run_id,
                    step.id,
                    latency_ms=latency_ms,
                    telemetry=output.telemetry,
                )
            except BaseException as exc:
                await self.fail_step(
                    run_id,
                    step.id,
                    latency_ms=latency_ms,
                    error_code=self._error_code(exc),
                    error_message=(
                        str(exc) if isinstance(exc, AgentError) else type(exc).__name__
                    ),
                )
                raise
        return output.value

    async def run_with_step(
        self,
        run_id: UUID,
        node_name: str,
        operation: Callable[[UUID], Awaitable[NodeOutput[T]]],
        *,
        attempt: int = 1,
    ) -> T:
        """Run a node whose bounded operation needs its persisted step id for Tool traces."""
        self._budget.check()
        step = await self.start_step(run_id, node_name, attempt)
        started = monotonic()
        try:
            timeout = min(self._node_timeouts[node_name], self._budget.remaining_seconds())
            output = await asyncio.wait_for(operation(step.id), timeout=timeout)
            self._budget.check()
        except TimeoutError as exc:
            await self.fail_step(
                run_id,
                step.id,
                latency_ms=int((monotonic() - started) * 1000),
                error_code="NODE_TIMEOUT",
                error_message=f"{node_name} exceeded its timeout",
            )
            raise AgentDeadlineExceededError from exc
        except BaseException as exc:
            code = self._error_code(exc)
            error_message = str(exc) if isinstance(exc, AgentError) else type(exc).__name__
            await self.fail_step(
                run_id,
                step.id,
                latency_ms=int((monotonic() - started) * 1000),
                error_code=code,
                error_message=error_message,
            )
            raise
        traced_latency = output.telemetry.trace_data.get("latency_ms")
        latency_ms = max(
            int((monotonic() - started) * 1000),
            traced_latency if isinstance(traced_latency, int) else 0,
        )
        try:
            await self._complete_step(
                    run_id,
                    step.id,
                    latency_ms=latency_ms,
                    telemetry=output.telemetry,
                )
        except BaseException as exc:
            await self.fail_step(
                run_id,
                step.id,
                latency_ms=latency_ms,
                error_code=self._error_code(exc),
                error_message=(
                    str(exc) if isinstance(exc, AgentError) else type(exc).__name__
                ),
            )
            raise
        return output.value

    async def start_step(self, run_id: UUID, node_name: str, attempt: int = 1) -> AgentStep:
        async with self._session_factory() as session:
            async with session_transaction(session):
                run = await session.scalar(
                    select(AgentRun).where(AgentRun.id == run_id).with_for_update()
                )
                if run is None or run.status not in {"pending", "running"}:
                    raise RuntimeError("Run is not active")
                self._assert_lease_owner(run)
                step = await TraceRecorder(session).start_step(run_id, node_name, attempt)
                await EventRecorder(session).record(
                    run_id,
                    "node.started",
                    {
                        "node_name": node_name,
                        "step_sequence": step.sequence,
                        "attempt": attempt,
                    },
                )
                return step

    async def _complete_step(
        self,
        run_id: UUID,
        step_id: UUID,
        *,
        latency_ms: int,
        telemetry: NodeTelemetry,
    ) -> None:
        async with self._session_factory() as session:
            async with session_transaction(session):
                run = await session.scalar(
                    select(AgentRun).where(AgentRun.id == run_id).with_for_update()
                )
                if run is None:
                    raise RuntimeError("Run disappeared")
                self._assert_lease_owner(run)
                step = await session.get(AgentStep, step_id)
                if step is None:
                    raise RuntimeError("step disappeared")
                await TraceRecorder(session).complete_step(
                    step,
                    status="completed",
                    latency_ms=latency_ms,
                    trace_data=telemetry.trace_data,
                    tokens_in=telemetry.tokens_in,
                    tokens_out=telemetry.tokens_out,
                    cost_cny=telemetry.cost_cny,
                    model_id=telemetry.model_id,
                    prompt_version=telemetry.prompt_version,
                )
                await EventRecorder(session).record(
                    run_id,
                    "node.completed",
                    {
                        "node_name": step.node_name,
                        "step_sequence": step.sequence,
                        "status": "completed",
                        "latency_ms": latency_ms,
                    },
                )

    async def fail_step(
        self,
        run_id: UUID,
        step_id: UUID,
        *,
        latency_ms: int,
        error_code: str,
        error_message: str,
    ) -> None:
        async with self._session_factory() as session:
            async with session_transaction(session):
                run = await session.scalar(
                    select(AgentRun).where(AgentRun.id == run_id).with_for_update()
                )
                if run is None:
                    return
                self._assert_lease_owner(run, allow_cancel_requested=True)
                step = await session.get(AgentStep, step_id)
                if step is None or step.status != "running":
                    return
                await TraceRecorder(session).complete_step(
                    step,
                    status="failed",
                    latency_ms=latency_ms,
                    trace_data={},
                    error_code=error_code,
                    error_message=error_message,
                )
                await EventRecorder(session).record(
                    run_id,
                    "node.completed",
                    {
                        "node_name": step.node_name,
                        "step_sequence": step.sequence,
                        "status": "failed",
                        "latency_ms": latency_ms,
                    },
                )

    def _assert_lease_owner(
        self,
        run: AgentRun,
        *,
        allow_cancel_requested: bool = False,
    ) -> None:
        if self._worker_id is None:
            return
        if (
            run.status != "running"
            or run.worker_id != self._worker_id
            or run.attempt_count != self._attempt_count
            or run.lease_expires_at is None
            or run.lease_expires_at <= datetime.now(UTC)
        ):
            raise AgentLeaseLostError("Agent Run lease ownership was lost")
        if run.cancel_requested_at is not None and not allow_cancel_requested:
            raise RunCancelledError("Agent Run cancellation was requested")

    def _error_code(self, exc: BaseException) -> str:
        if isinstance(exc, AgentError):
            return exc.code
        if isinstance(exc, asyncio.CancelledError) and self._budget.cancelled:
            return "RUN_CANCELLED"
        return "NODE_EXECUTION_FAILED"
