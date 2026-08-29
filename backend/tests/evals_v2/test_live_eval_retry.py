from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.errors import (
    AgentError,
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderRetriesExhaustedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    StructuredOutputError,
)
from app.harness.provider_calls.audit import AuditSearchProvider
from app.harness.provider_calls.recorder import ProviderCallRecorder
from app.harness.provider_calls.retry import (
    LiveEvalCallController,
    LiveEvalRetryPolicy,
    RetryAttemptState,
    RetryingSearchProvider,
)
from app.providers.search import SearchResultItem


class SequenceSearchProvider:
    provider_name = "fake-live"

    def __init__(self, outcomes: Sequence[AgentError | list[SearchResultItem]]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    async def search(
        self, *, query: str, limit: int, freshness_days: int | None
    ) -> list[SearchResultItem]:
        del query, limit, freshness_days
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, AgentError):
            raise outcome
        return outcome


def build_retrying_search(
    inner: SequenceSearchProvider | AuditSearchProvider,
    *,
    state: RetryAttemptState | None = None,
    attempts: int = 3,
    base_seconds: float = 0,
    max_seconds: float = 0,
    deadline_seconds: float = 1,
) -> RetryingSearchProvider:
    return RetryingSearchProvider(
        inner,
        controller=LiveEvalCallController(concurrency=2, pacing_seconds=0),
        policy=LiveEvalRetryPolicy(
            max_attempts=attempts,
            base_seconds=base_seconds,
            max_seconds=max_seconds,
            jitter_ratio=0,
        ),
        deadline_seconds=deadline_seconds,
        attempt_state=state or RetryAttemptState(),
        random_source=lambda: 0,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first_error",
    [
        ProviderRateLimitError("limited", retryable=True),
        ProviderTimeoutError("timeout", retryable=True),
        ProviderUnavailableError("5xx", retryable=True),
    ],
)
async def test_transient_provider_error_retries_then_succeeds(
    first_error: AgentError,
) -> None:
    inner = SequenceSearchProvider([first_error, []])
    result = await build_retrying_search(inner).search(
        query="jobs", limit=1, freshness_days=None
    )
    assert result == []
    assert inner.calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        ProviderAuthenticationError("unauthorized"),
        StructuredOutputError("schema"),
        ProviderUnavailableError("bad request", retryable=False),
    ],
)
async def test_non_transient_provider_error_is_not_retried(error: AgentError) -> None:
    inner = SequenceSearchProvider([error, []])
    with pytest.raises(type(error)):
        await build_retrying_search(inner).search(
            query="jobs", limit=1, freshness_days=None
        )
    assert inner.calls == 1


@pytest.mark.asyncio
async def test_max_attempts_raises_exhausted_failure() -> None:
    inner = SequenceSearchProvider(
        [ProviderTimeoutError("timeout", retryable=True) for _ in range(3)]
    )
    with pytest.raises(ProviderRetriesExhaustedError):
        await build_retrying_search(inner).search(
            query="jobs", limit=1, freshness_days=None
        )
    assert inner.calls == 3


def test_retry_after_takes_priority_over_exponential_backoff() -> None:
    policy = LiveEvalRetryPolicy(
        max_attempts=3,
        base_seconds=1,
        max_seconds=8,
        jitter_ratio=0.25,
    )
    error = ProviderRateLimitError(
        "limited", retryable=True, retry_after_seconds=3.5
    )
    assert policy.delay_seconds(
        retry_index=2, error=error, random_value=1
    ) == 3.5


@pytest.mark.asyncio
async def test_cancellation_interrupts_backoff() -> None:
    started = asyncio.Event()

    class SignallingProvider(SequenceSearchProvider):
        async def search(
            self, *, query: str, limit: int, freshness_days: int | None
        ) -> list[SearchResultItem]:
            started.set()
            return await super().search(
                query=query, limit=limit, freshness_days=freshness_days
            )

    inner = SignallingProvider(
        [ProviderRateLimitError("limited", retryable=True, retry_after_seconds=5)]
    )
    task = asyncio.create_task(
        build_retrying_search(
            inner, base_seconds=5, max_seconds=5, deadline_seconds=10
        ).search(query="jobs", limit=1, freshness_days=None)
    )
    await started.wait()
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert inner.calls == 1


@pytest.mark.asyncio
async def test_retry_stops_when_deadline_cannot_fit_backoff() -> None:
    inner = SequenceSearchProvider(
        [ProviderTimeoutError("timeout", retryable=True, retry_after_seconds=1)]
    )
    with pytest.raises(ProviderRetriesExhaustedError, match="deadline"):
        await build_retrying_search(
            inner, base_seconds=1, max_seconds=1, deadline_seconds=0.01
        ).search(query="jobs", limit=1, freshness_days=None)
    assert inner.calls == 1


@pytest.mark.asyncio
async def test_every_physical_retry_attempt_is_audited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = RetryAttemptState()
    inner = SequenceSearchProvider(
        [ProviderRateLimitError("limited", retryable=True), []]
    )
    recorder = ProviderCallRecorder(
        session_factory=cast(async_sessionmaker[AsyncSession], object()),
        run_id=uuid4(),
        trial_id=uuid4(),
    )
    attempts: list[int] = []

    async def capture_persist(**kwargs: object) -> None:
        retry_attempt = kwargs["retry_attempt"]
        assert isinstance(retry_attempt, int)
        attempts.append(retry_attempt)

    monkeypatch.setattr(recorder, "_persist", capture_persist)
    audited = AuditSearchProvider(
        inner,
        recorder,
        retry_attempt_getter=state.get,
    )
    result = await build_retrying_search(audited, state=state).search(
        query="jobs", limit=1, freshness_days=None
    )
    assert result == []
    assert attempts == [0, 1]


@pytest.mark.asyncio
async def test_controller_enforces_concurrency_and_start_pacing() -> None:
    controller = LiveEvalCallController(concurrency=1, pacing_seconds=0.01)
    active = 0
    maximum_active = 0
    starts: list[float] = []
    loop = asyncio.get_running_loop()

    async def physical_call() -> None:
        nonlocal active, maximum_active
        async with controller.slot(deadline=loop.time() + 1):
            starts.append(loop.time())
            active += 1
            maximum_active = max(maximum_active, active)
            await asyncio.sleep(0.02)
            active -= 1

    await asyncio.gather(physical_call(), physical_call())
    assert maximum_active == 1
    assert starts[1] - starts[0] >= 0.01
