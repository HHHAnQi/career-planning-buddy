"""Unit tests for LocalEmbeddingProvider boundary behaviour.

Mock embedding is well-covered. The local-weights Provider is what real
deployments use, but its integration tests are skipped in CI. Here we lock
the configuration and dimension-guard logic without depending on torch.
"""

import asyncio
from pathlib import Path

import pytest

from app.agent.errors import ProviderConfigurationError, ProviderUnavailableError
from app.providers.embedding import LocalEmbeddingProvider, MockEmbeddingProvider


def test_local_provider_reports_metadata_without_loading() -> None:
    # 不触发 _load()，metadata 字段应立即可读
    provider = LocalEmbeddingProvider(
        model_path=Path("/nonexistent/bge-m3"),
        dimension=1024,
        model_name="BAAI/bge-m3",
    )
    assert provider.provider_name == "local"
    assert provider.dimension == 1024
    assert provider.model_name == "BAAI/bge-m3"


@pytest.mark.asyncio
async def test_local_provider_embed_empty_returns_empty_list() -> None:
    provider = LocalEmbeddingProvider(
        model_path=Path("/nonexistent"), dimension=1024, model_name="x"
    )
    # 不应触发模型加载 —— 零文本应短路
    assert await provider.embed([]) == []


@pytest.mark.asyncio
async def test_local_provider_raises_configuration_error_on_missing_weights() -> None:
    provider = LocalEmbeddingProvider(
        model_path=Path("/definitely/not/a/model/path"), dimension=1024, model_name="x"
    )
    with pytest.raises(ProviderConfigurationError):
        await provider.embed(["something"])


@pytest.mark.asyncio
async def test_local_provider_warmup_is_single_and_embedding_degrades_while_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = LocalEmbeddingProvider(
        model_path=Path("/nonexistent"), dimension=1024, model_name="x"
    )
    release = asyncio.Event()
    load_calls = 0

    async def slow_load(_provider: LocalEmbeddingProvider) -> object:
        nonlocal load_calls
        load_calls += 1
        await release.wait()
        return object()

    monkeypatch.setattr(LocalEmbeddingProvider, "_load", slow_load)
    first_task = provider.start_warmup()
    second_task = provider.start_warmup()
    await asyncio.sleep(0)

    assert first_task is second_task
    with pytest.raises(ProviderUnavailableError, match="warming up"):
        await provider.embed(["query"])

    release.set()
    await first_task
    assert load_calls == 1


def test_mock_embedding_is_deterministic_and_normalized() -> None:
    provider = MockEmbeddingProvider(dimension=1024)
    a = provider._vector("hello")  # noqa: SLF001
    b = provider._vector("hello")
    c = provider._vector("world")
    assert a == b, "same text must hash to same vector"
    assert a != c, "different text must produce different vector"
    # L2 范数应为 1（cosine 友好）
    norm = sum(v * v for v in a) ** 0.5
    assert abs(norm - 1.0) < 1e-6


@pytest.mark.asyncio
async def test_mock_embedding_respects_dimension() -> None:
    provider = MockEmbeddingProvider(dimension=8)
    vectors = await provider.embed(["a", "b"])
    assert len(vectors) == 2
    assert all(len(v) == 8 for v in vectors)
