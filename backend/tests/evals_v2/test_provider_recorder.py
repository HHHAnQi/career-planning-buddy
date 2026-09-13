"""Focused regression tests for ProviderCallRecorder transparency."""

import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.agent.errors import ProviderTimeoutError
from app.harness.provider_calls.recorder import ProviderCallRecorder


@pytest.mark.asyncio
async def test_recorder_persists_then_reraises_agent_error() -> None:
    recorder = ProviderCallRecorder(
        session_factory=MagicMock(),
        run_id=uuid4(),
    )
    persist = AsyncMock()
    recorder._persist = persist  # type: ignore[method-assign]

    async def timeout() -> object:
        raise ProviderTimeoutError("provider timed out")

    with pytest.raises(ProviderTimeoutError):
        await recorder.invoke(
            provider_kind="llm",
            provider_method="generate_plan",
            retry_attempt=0,
            request_projection={"method": "generate_plan"},
            coro_factory=timeout,
            respond_projection=lambda _: {},
        )

    persist.assert_awaited_once()
    assert persist.await_args is not None
    persisted = persist.await_args.kwargs
    assert persisted["status"] == "error"
    assert persisted["error_code"] == "PROVIDER_TIMEOUT"


@pytest.mark.asyncio
async def test_recorder_persists_cancelled_call_then_reraises() -> None:
    recorder = ProviderCallRecorder(
        session_factory=MagicMock(),
        run_id=uuid4(),
    )
    persist = AsyncMock()
    recorder._persist = persist  # type: ignore[method-assign]

    async def cancelled() -> object:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await recorder.invoke(
            provider_kind="llm",
            provider_method="generate_agent_turn",
            retry_attempt=0,
            request_projection={"method": "generate_agent_turn"},
            coro_factory=cancelled,
            respond_projection=lambda _: {},
        )

    persist.assert_awaited_once()
    assert persist.await_args is not None
    persisted = persist.await_args.kwargs
    assert persisted["status"] == "cancelled"
    assert persisted["error_code"] is None
