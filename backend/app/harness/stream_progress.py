"""Throttled ``llm.stream.progress`` events for wire-level token streaming.

Bound per LLM call by the planning graph via
``app.providers.streaming.bind_stream_delta_sink``. Every content delta
updates in-memory counters; at most one durable event per
``min_interval_seconds`` is appended through ``EventRecorder`` so the SSE
poller can surface live progress. Raw delta text is NEVER persisted —
only cumulative counts — keeping ``agent_events`` small and free of
partial JSON.

All persistence is best-effort: a terminal/cancelled Run or a transient
database error disables the publisher instead of breaking the stream.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from time import monotonic
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import session_transaction
from app.harness.events import EventRecorder

logger = logging.getLogger(__name__)

Clock = Callable[[], float]


class StreamProgressPublisher:
    """One publisher per LLM call; also captures streaming step metrics."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        run_id: UUID,
        node_name: str,
        turn: int,
        min_interval_seconds: float = 0.5,
        clock: Clock = monotonic,
    ) -> None:
        self._session_factory = session_factory
        self._run_id = run_id
        self._node_name = node_name
        self._turn = turn
        self._min_interval = min_interval_seconds
        self._clock = clock
        self._started_at = clock()
        self._first_delta_at: float | None = None
        self._last_emit_at: float | None = None
        self._chunk_count = 0
        self._chars = 0
        self._event_count = 0
        self._disabled = False

    async def on_delta(self, text: str) -> None:
        """StreamDeltaSink implementation; must never raise."""

        now = self._clock()
        if self._first_delta_at is None:
            self._first_delta_at = now
        self._chunk_count += 1
        self._chars += len(text)
        if self._disabled:
            return
        if (
            self._last_emit_at is not None
            and (now - self._last_emit_at) < self._min_interval
        ):
            return
        await self._emit(now)

    async def _emit(self, now: float) -> None:
        try:
            async with self._session_factory() as session:
                async with session_transaction(session):
                    await EventRecorder(session).record(
                        self._run_id,
                        "llm.stream.progress",
                        {
                            "node": self._node_name,
                            "turn": self._turn,
                            "chunk_count": self._chunk_count,
                            "cumulative_chars": self._chars,
                        },
                    )
        except Exception:  # noqa: BLE001 - progress is best-effort by contract
            self._disabled = True
            logger.debug(
                "llm.stream.progress disabled for run %s (terminal or db error)",
                self._run_id,
            )
            return
        self._last_emit_at = now
        self._event_count += 1

    def summary(self) -> dict[str, Any]:
        """Step-telemetry snapshot of this stream, whether or not it emitted."""

        first_token_latency_ms: int | None = None
        if self._first_delta_at is not None:
            first_token_latency_ms = int((self._first_delta_at - self._started_at) * 1000)
        return {
            "node": self._node_name,
            "turn": self._turn,
            "chunk_count": self._chunk_count,
            "chars": self._chars,
            "first_token_latency_ms": first_token_latency_ms,
            "progress_events": self._event_count,
            "streamed": self._chunk_count > 0,
        }
