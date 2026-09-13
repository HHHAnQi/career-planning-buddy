"""Tests for the OpenAI-compatible hosted embedding provider.

Pins:
* Request shape: model, input batch, explicit dimensions=1024.
* Response parsing: order-preserving, dimension-checked vectors.
* Batch splitting beyond _MAX_BATCH.
* Error mapping: 401 → auth, 429 → rate limit with retry-after,
  dimension mismatch → non-retryable unavailable.
* Transient timeout retries then succeeds.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.agent.errors import (
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from app.providers.embedding_api import (
    _MAX_BATCH,
    OpenAICompatibleEmbeddingProvider,
)


def _provider(handler) -> OpenAICompatibleEmbeddingProvider:  # type: ignore[no-untyped-def]
    return OpenAICompatibleEmbeddingProvider(
        api_key="test-only",
        base_url="https://embedding.example.test/v4",
        model="embedding-3",
        dimension=1024,
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )


def _ok_response(count: int) -> dict[str, object]:
    return {
        "data": [
            {"index": i, "embedding": [0.1, 0.2] * 512} for i in range(count)
        ]
    }


@pytest.mark.asyncio
async def test_request_shape_and_parsing() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_ok_response(2))

    provider = _provider(handler)
    vectors = await provider.embed(["你好", "hello"])
    assert captured["model"] == "embedding-3"
    assert captured["input"] == ["你好", "hello"]
    assert captured["dimensions"] == 1024
    assert len(vectors) == 2 and all(len(v) == 1024 for v in vectors)


@pytest.mark.asyncio
async def test_batch_splitting() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(len(body["input"]))
        return httpx.Response(200, json=_ok_response(len(body["input"])))

    provider = _provider(handler)
    texts = [f"t{i}" for i in range(_MAX_BATCH + 5)]
    vectors = await provider.embed(texts)
    assert calls == [_MAX_BATCH, 5]
    assert len(vectors) == _MAX_BATCH + 5


@pytest.mark.asyncio
async def test_error_mapping() -> None:
    provider = _provider(lambda r: httpx.Response(401))
    with pytest.raises(ProviderAuthenticationError):
        await provider.embed(["x"])

    provider = _provider(
        lambda r: httpx.Response(429, headers={"retry-after": "2"})
    )
    with pytest.raises(ProviderRateLimitError):
        await provider.embed(["x"])

    def bad_dim(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}]})

    provider = _provider(bad_dim)
    with pytest.raises(ProviderUnavailableError):
        await provider.embed(["x"])


@pytest.mark.asyncio
async def test_transient_failure_retries_then_succeeds() -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json=_ok_response(1))

    provider = _provider(handler)
    vectors = await provider.embed(["x"])
    assert len(vectors) == 1
    assert len(attempts) == 2
