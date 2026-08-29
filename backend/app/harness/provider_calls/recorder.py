"""Run-global ProviderCall recorder.

A new ``ProviderCallRecorder`` is constructed per Run by the TrialRunner
_before_ the executor starts running the graph. Each ``invoke()`` call:

1. allocates the next run-global ``sequence`` (atomic ``+1`` in-process),
2. computes the request projection's ``canonical_sha256``,
3. times + awaits the inner provider call,
4. projects the response (or error) + computes its hash,
5. persists a ``ProviderCall`` row through a fresh session_transaction
   (so it can commit while the graph's own transaction is mid-flight;
   keepalive rows are visible to ``collect_outcome`` at the end).

The recorder stays a pure writer -- it does not perform retry, fallback,
or deserialization. Avoiding mutation of the inner call's semantics is the
contract that lets ``AuditProvider`` be transparent in mock mode and lets
``FixtureProvider`` subclass the same shape for replay.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.errors import AgentError
from app.core.database import session_transaction
from app.models.provider_call import ProviderCall
from evals.v2.contracts import canonical_sha256

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

# 14-min cap mirrors the executor's budget; provider hangs (e.g. [mock:timeout]
# inside MockPlanningProvider) cancel out through the graph and finalize the
# Run as cancelled/failed BEFORE this safety bounds, so this is just a
# best-effort guard against runaway test sessions.
_PROVIDER_INVOKE_HARD_CAP_SECONDS = 600.0


@dataclass
class ProviderInvocationResult:
    """Materialised view of one call: passed to caller + persisted as a row."""

    sequence: int
    provider_kind: str
    provider_method: str
    request_projection: dict[str, object]
    request_projection_hash: str
    response_projection: dict[str, object] | None
    response_projection_hash: str | None
    status: str  # "ok" | "error" | "cancelled"
    error_code: str | None
    tokens_in: int | None
    tokens_out: int | None
    latency_ms: int
    model_id: str | None


@dataclass
class ProviderCallRecorder:
    """One recorder per Run. Owns the run-global call sequence."""

    session_factory: async_sessionmaker[AsyncSession]
    run_id: UUID
    trial_id: UUID | None = None
    model_id: str | None = "mock-career-planner-v1"
    _sequence: int = field(default=0, init=False)
    _method_index: dict[tuple[str, str], int] = field(
        default_factory=dict, init=False
    )

    def allocate_sequence(
        self, *, provider_kind: str, provider_method: str, retry_attempt: int
    ) -> tuple[int, int]:
        """Reserve the next run-global sequence + logical index.

        ``retry_attempt`` is informational only; the logical index is keyed
        on ``(provider_kind, provider_method)`` so multiple repair attempts
        of the same logical call stack without exhausting it (e.g.
        ``[mock:invalid-schema-twice]``) are distinguishable from unrelated
        calls.
        """

        sequence = self._sequence
        self._sequence += 1
        key = (provider_kind, provider_method)
        logical = self._method_index.get(key, 0)
        if retry_attempt == 0:
            self._method_index[key] = logical + 1
        return sequence, logical

    async def invoke(
        self,
        *,
        provider_kind: str,
        provider_method: str,
        retry_attempt: int,
        request_projection: dict[str, object],
        coro_factory: Callable[[], Awaitable[object]],
        respond_projection: Callable[[object], dict[str, object]],
    ) -> ProviderInvocationResult:
        """Run + record one provider call. Returns the materialised result."""

        sequence, logical = self.allocate_sequence(
            provider_kind=provider_kind,
            provider_method=provider_method,
            retry_attempt=retry_attempt,
        )
        request_hash = canonical_sha256(request_projection)
        start = time.perf_counter()
        status = "ok"
        error_code: str | None = None
        response_proj: dict[str, object] | None = None
        response_hash: str | None = None
        tokens_in: int | None = None
        tokens_out: int | None = None
        agent_error: AgentError | None = None
        try:
            raw_response = await coro_factory()
        except AgentError as exc:
            status = "error"
            # PR-9b: persist ``exc.code`` (e.g. ``"PROVIDER_UNAVAILABLE"``)
            # so the audit ledger stays joinable with
            # ``app.harness.errors.EvalFailureCode`` and
            # ``stats.runtime_failure_codes()``. Previously this wrote
            # ``type(exc).__name__`` (the Python class name) which never
            # matched the StatsShell taxonomy.
            error_code = exc.code
            agent_error = exc
        except asyncio.CancelledError:
            # The CancelledError is re-raised after we persist the row so the
            # executor's cancel-cooperative path is preserved exactly. The
            # schema reserves error_code for status="error"; cancellation is
            # represented by its distinct status and therefore stores NULL.
            status = "cancelled"
            error_code = None
            try:
                await self._persist(
                    sequence=sequence,
                    provider_kind=provider_kind,
                    provider_method=provider_method,
                    logical=logical,
                    retry_attempt=retry_attempt,
                    request_projection=request_projection,
                    request_hash=request_hash,
                    response_proj=None,
                    response_hash=None,
                    status=status,
                    error_code=error_code,
                    tokens_in=None,
                    tokens_out=None,
                    latency_ms=int((time.perf_counter() - start) * 1000),
                    model_id=self.model_id,
                )
            except Exception:  # noqa: BLE001
                pass
            raise

        latency_ms = int((time.perf_counter() - start) * 1000)

        if status == "ok":
            response_proj = respond_projection(raw_response)
            response_hash = canonical_sha256(response_proj)
            usage_raw = response_proj.get("usage")
            if isinstance(usage_raw, dict):
                tokens_in_val = usage_raw.get("tokens_in")
                tokens_out_val = usage_raw.get("tokens_out")
                if isinstance(tokens_in_val, int | float):
                    tokens_in = int(tokens_in_val)
                if isinstance(tokens_out_val, int | float):
                    tokens_out = int(tokens_out_val)

        await self._persist(
            sequence=sequence,
            provider_kind=provider_kind,
            provider_method=provider_method,
            logical=logical,
            retry_attempt=retry_attempt,
            request_projection=request_projection,
            request_hash=request_hash,
            response_proj=response_proj,
            response_hash=response_hash,
            status=status,
            error_code=error_code,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            model_id=self.model_id if provider_kind == "llm" else None,
        )

        if agent_error is not None:
            raise agent_error

        return ProviderInvocationResult(
            sequence=sequence,
            provider_kind=provider_kind,
            provider_method=provider_method,
            request_projection=request_projection,
            request_projection_hash=request_hash,
            response_projection=response_proj,
            response_projection_hash=response_hash,
            status=status,
            error_code=error_code,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            model_id=self.model_id,
        )

    async def _persist(
        self,
        *,
        sequence: int,
        provider_kind: str,
        provider_method: str,
        logical: int,
        retry_attempt: int,
        request_projection: dict[str, object],
        request_hash: str,
        response_proj: dict[str, object] | None,
        response_hash: str | None,
        status: str,
        error_code: str | None,
        tokens_in: int | None,
        tokens_out: int | None,
        latency_ms: int,
        model_id: str | None,
    ) -> None:
        row = ProviderCall(
            id=uuid4(),
            run_id=self.run_id,
            trial_id=self.trial_id,
            sequence=sequence,
            provider_kind=provider_kind,
            provider_method=provider_method,
            logical_call_index=logical,
            retry_attempt=retry_attempt,
            request_projection=request_projection,
            request_projection_hash=request_hash,
            response_projection=response_proj,
            response_projection_hash=response_hash,
            status=status,
            error_code=error_code,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            model_id=model_id,
            created_at=datetime.now(UTC),
        )
        async with self.session_factory() as session:
            async with session_transaction(session):
                session.add(row)
                await session.flush()


def now_iso() -> str:
    """Stable ISO-8601 timestamp helper for projection helpers."""

    return datetime.now(UTC).isoformat()
