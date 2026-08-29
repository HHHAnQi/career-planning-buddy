"""Tests for wire-level streaming: sink plumbing, provider wiring, and the
throttled progress publisher.

Pins:
* ContextVar sink binding is scoped and restores the previous sink.
* ``OpenAICompatiblePlanningProvider`` streams (and forwards deltas to the
  bound sink) only when ``streaming_enabled``; otherwise it uses the
  blocking ``complete`` path regardless of sinks.
* ``StreamProgressPublisher`` emits the first delta immediately, throttles
  later ones, never persists raw delta text, and disables itself instead
  of raising when event recording fails (terminal run / db error).
* Publisher summaries expose chunk counts, chars, and first-token latency
  for step telemetry.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import pytest

import app.harness.stream_progress as stream_progress_module
from app.harness.stream_progress import StreamProgressPublisher
from app.providers.llm import OpenAICompatiblePlanningProvider
from app.providers.llm_contracts import LLMRequest, LLMResponse
from app.providers.streaming import (
    bind_stream_delta_sink,
    current_stream_delta_sink,
)


@pytest.mark.asyncio
async def test_sink_binding_is_scoped_and_restores_previous() -> None:
    async def first(_: str) -> None:
        return None

    async def second(_: str) -> None:
        return None

    assert current_stream_delta_sink() is None
    with bind_stream_delta_sink(first):
        assert current_stream_delta_sink() is first
        with bind_stream_delta_sink(second):
            assert current_stream_delta_sink() is second
        assert current_stream_delta_sink() is first
    assert current_stream_delta_sink() is None


class RecordingStreamClient:
    """LLMClient stand-in: records which path was used and drives the sink."""

    def __init__(self, *, content: str = '{"ok": true}') -> None:
        self.complete_calls = 0
        self.streamed_calls = 0
        self.sinks_used: list[Any] = []
        self._content = content

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.complete_calls += 1
        return self._response()

    async def complete_streamed(
        self, request: LLMRequest, *, on_delta: Any = None
    ) -> LLMResponse:
        self.streamed_calls += 1
        self.sinks_used.append(on_delta)
        if on_delta is not None:
            await on_delta(self._content)
        return self._response()

    def _response(self) -> LLMResponse:
        return LLMResponse(
            content=self._content,
            provider_id="openai_compatible",
            model_id="test-model",
            latency_ms=1,
            raw_output_hash="a" * 64,
        )

    async def aclose(self) -> None:
        return None


def _provider(
    client: RecordingStreamClient, *, streaming: bool
) -> OpenAICompatiblePlanningProvider:
    return OpenAICompatiblePlanningProvider(
        api_key="test-only",
        base_url="https://api.example.test/v1",
        model="test-model",
        client=client,
        streaming_enabled=streaming,
    )


def _planning_context() -> Any:
    from datetime import date

    from app.schemas.agent_runs import (
        PlanningContext,
        PlanningWindow,
        ProfileContext,
    )

    today = date(2026, 8, 23)
    return PlanningContext(
        profile=ProfileContext(
            user_id=uuid4(),
            version=1,
            goal_type="agent_app",
            stage="preparing",
            time_budget_minutes=120,
            skill_level="intermediate",
            skill_summary="test",
        ),
        planning_window=PlanningWindow(
            planning_date=today,
            horizon_start=today,
            horizon_end=today,
            horizon_weeks=1,
        ),
        time_budget_minutes=120,
        token_estimate=100,
    )


@pytest.mark.asyncio
async def test_provider_streams_and_forwards_sink_when_enabled() -> None:
    client = RecordingStreamClient()
    provider = _provider(client, streaming=True)
    received: list[str] = []

    async def sink(text: str) -> None:
        received.append(text)

    with bind_stream_delta_sink(sink):
        result = await provider.generate_plan(
            message="plan",
            context=_planning_context(),
            replan_mode="initial",
            evidence_catalog=[],
        )
    assert "candidate" in result
    assert client.streamed_calls == 1
    assert client.complete_calls == 0
    assert client.sinks_used == [sink]
    assert received == ['{"ok": true}']


@pytest.mark.asyncio
async def test_provider_blocks_when_streaming_disabled() -> None:
    client = RecordingStreamClient()
    provider = _provider(client, streaming=False)

    async def sink(_: str) -> None:
        raise AssertionError("sink must not be called on the blocking path")

    with bind_stream_delta_sink(sink):
        await provider.generate_plan(
            message="plan",
            context=_planning_context(),
            replan_mode="initial",
            evidence_catalog=[],
        )
    assert client.complete_calls == 1
    assert client.streamed_calls == 0


class _SessionStub:
    """Minimal async session context for the publisher."""

    def __init__(self) -> None:
        self.entered = 0

    async def __aenter__(self) -> _SessionStub:
        self.entered += 1
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


@pytest.mark.asyncio
async def test_publisher_throttles_events_and_hides_raw_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict[str, object]]] = []
    # Fake clock: emit(t=0), skip(0.1), skip(0.2), emit(0.6), emit(1.2).
    times = iter([0.0, 0.0, 0.1, 0.2, 0.6, 1.2, 2.0])
    clock = lambda: next(times)  # noqa: E731

    @asynccontextmanager
    async def fake_transaction(session: object) -> Any:
        yield

    class FakeRecorder:
        def __init__(self, session: object) -> None:
            pass

        async def record(
            self, run_id: object, event_type: str, payload: dict[str, object]
        ) -> None:
            events.append((event_type, payload))

    monkeypatch.setattr(stream_progress_module, "session_transaction", fake_transaction)
    monkeypatch.setattr(stream_progress_module, "EventRecorder", FakeRecorder)

    factory = lambda: _SessionStub()  # noqa: E731
    publisher = StreamProgressPublisher(
        session_factory=factory,  # type: ignore[arg-type]
        run_id=uuid4(),
        node_name="career_planning_agent",
        turn=1,
        min_interval_seconds=0.5,
        clock=clock,
    )
    for text in ["aaaa", "bbbb", "cccc", "dddd", "eeee"]:
        await publisher.on_delta(text)

    assert len(events) == 3
    assert all(kind == "llm.stream.progress" for kind, _ in events)
    payloads = [payload for _, payload in events]
    # Counts only — raw delta text is never persisted.
    assert payloads[0]["cumulative_chars"] == 4
    assert payloads[0]["chunk_count"] == 1
    assert payloads[1]["cumulative_chars"] == 16
    assert payloads[1]["chunk_count"] == 4
    assert payloads[2]["cumulative_chars"] == 20
    assert payloads[2]["chunk_count"] == 5
    assert "aaaa" not in str(payloads)

    summary = publisher.summary()
    assert summary["chunk_count"] == 5
    assert summary["chars"] == 20
    assert summary["streamed"] is True
    assert summary["progress_events"] == 3
    assert summary["first_token_latency_ms"] == 0


@pytest.mark.asyncio
async def test_publisher_disables_itself_on_recording_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @asynccontextmanager
    async def failing_transaction(session: object) -> Any:
        raise RuntimeError("database unavailable")
        yield  # pragma: no cover

    monkeypatch.setattr(
        stream_progress_module, "session_transaction", failing_transaction
    )

    factory = lambda: _SessionStub()  # noqa: E731
    publisher = StreamProgressPublisher(
        session_factory=factory,  # type: ignore[arg-type]
        run_id=uuid4(),
        node_name="career_planning_agent",
        turn=1,
    )
    # Must not raise despite the failing transaction.
    await publisher.on_delta("hello")
    await publisher.on_delta("world")
    summary = publisher.summary()
    assert summary["chunk_count"] == 2
    assert summary["chars"] == 10
    assert summary["progress_events"] == 0
