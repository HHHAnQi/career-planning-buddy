"""Rerank Provider protocol plus deterministic Mock and TEI HTTP implementations.

Reranking is the second stage of the retrieval pipeline: hybrid RRF
recall feeds the top-N candidates, the reranker re-scores each
(query, chunk) pair, and low scores are rejected by the service's
answerability gate.

* ``MockRerankProvider`` — deterministic lexical-overlap scores so the
  whole pipeline stays reproducible in CI (Mock never calls the network).
* ``TeiRerankProvider`` — HuggingFace ``text-embeddings-inference``
  ``/rerank`` endpoint (e.g. the local bge-reranker service), the de
  facto reranker serving API.

Selection: ``RERANK_PROVIDER=mock|tei``; ``tei`` fails closed without
``RERANK_BASE_URL`` — never silently substituting Mock (project rule).
"""

from __future__ import annotations

from typing import Protocol

import httpx

from app.agent.errors import (
    AgentError,
    ProviderConfigurationError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.agent.resume_context_selection import lexical_similarity
from app.core.config import Settings


class RerankProvider(Protocol):
    provider_name: str

    async def rerank(self, query: str, texts: list[str]) -> list[float]:
        """Return one relevance score per text, in input order."""
        ...

    async def aclose(self) -> None: ...


class MockRerankProvider:
    """Deterministic lexical-overlap reranker for tests and Mock mode."""

    provider_name = "mock"

    async def rerank(self, query: str, texts: list[str]) -> list[float]:
        return [min(1.0, lexical_similarity(query, text) * 2.0) for text in texts]

    async def aclose(self) -> None:
        return None


class TeiRerankProvider:
    """HuggingFace text-embeddings-inference ``/rerank`` client."""

    provider_name = "tei"

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float = 8.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not base_url:
            raise ProviderConfigurationError("tei reranker requires RERANK_BASE_URL")
        self._endpoint = f"{base_url.rstrip('/')}/rerank"
        self._client = httpx.AsyncClient(
            timeout=timeout_seconds,
            transport=transport,
            headers={"Content-Type": "application/json"},
        )

    async def rerank(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        try:
            response = await self._client.post(
                self._endpoint,
                json={"query": query, "texts": texts},
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                "reranker request timed out", retryable=True
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderUnavailableError(
                "reranker could not be reached", retryable=True
            ) from exc
        if response.status_code in {401, 403}:
            raise ProviderConfigurationError("reranker rejected authentication")
        if response.status_code >= 400:
            raise ProviderUnavailableError(
                f"reranker returned HTTP {response.status_code}", retryable=False
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderUnavailableError(
                "reranker returned invalid JSON", retryable=False
            ) from exc
        # TEI: [{"index": 0, "score": 0.98}, ...] — normalize to input order.
        scores = [0.0] * len(texts)
        for item in payload if isinstance(payload, list) else []:
            if isinstance(item, dict) and isinstance(item.get("index"), int):
                index = item["index"]
                if 0 <= index < len(scores):
                    score = item.get("score")
                    scores[index] = float(score) if isinstance(score, (int, float)) else 0.0
        return scores

    async def aclose(self) -> None:
        await self._client.aclose()


def build_rerank_provider(settings: Settings) -> RerankProvider:
    """Build exactly the configured reranker; never silently substitute Mock."""

    if settings.rerank_provider == "mock":
        return MockRerankProvider()
    if settings.rerank_base_url is None:
        raise ProviderConfigurationError(
            "tei reranker requires RERANK_BASE_URL"
        )
    return TeiRerankProvider(
        base_url=str(settings.rerank_base_url),
        timeout_seconds=settings.rerank_timeout_seconds,
    )


__all__ = [
    "AgentError",
    "RerankProvider",
    "MockRerankProvider",
    "TeiRerankProvider",
    "build_rerank_provider",
]
