"""OpenAI-compatible hosted embedding adapter (e.g. Zhipu embedding-3).

Speaks the ``POST {base}/embeddings`` wire format with an explicit
``dimensions`` request so the returned vectors always match the pgvector
column contract (1024). Hosted embeddings cost per token; failures map
to the typed provider errors and never silently fall back to Mock.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from app.agent.errors import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    parse_retry_after,
)

logger = logging.getLogger(__name__)

_MAX_BATCH = 16
_RETRIES = 2


class OpenAICompatibleEmbeddingProvider:
    provider_name = "openai_compatible"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        dimension: int,
        timeout_seconds: float = 30,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key or not base_url or not model:
            raise ProviderConfigurationError(
                "openai_compatible embedding requires API key, base URL, and model"
            )
        self._endpoint = f"{base_url.rstrip('/')}/embeddings"
        self._api_key = api_key
        self._model = model
        self.dimension = dimension
        self._timeout = timeout_seconds
        self._transport = transport

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts in bounded batches; retries transient failures."""

        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), _MAX_BATCH):
            batch = texts[start : start + _MAX_BATCH]
            vectors.extend(await self._embed_batch(batch))
        return vectors

    async def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        body: dict[str, object] = {
            "model": self._model,
            "input": batch,
            "dimensions": self.dimension,
        }
        last_error: Exception | None = None
        for attempt in range(_RETRIES + 1):
            try:
                return await self._send(body)
            except (
                ProviderTimeoutError,
                ProviderUnavailableError,
            ) as error:
                last_error = error
                if not error.retryable or attempt == _RETRIES:
                    raise
                await asyncio.sleep(0.5 * (2**attempt))
        raise last_error or ProviderUnavailableError("embedding failed")

    async def _send(self, body: dict[str, object]) -> list[list[float]]:
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=self._timeout,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
            ) as client:
                response = await client.post(self._endpoint, json=body)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                "embedding request timed out", retryable=True
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderUnavailableError(
                "embedding provider unreachable", retryable=True
            ) from exc
        if response.status_code in {401, 403}:
            raise ProviderAuthenticationError("embedding auth rejected")
        if response.status_code == 429:
            raise ProviderRateLimitError(
                "embedding rate limited",
                retry_after_seconds=parse_retry_after(response.headers.get("retry-after")),
            )
        if response.status_code >= 500:
            raise ProviderUnavailableError(
                f"embedding HTTP {response.status_code}", retryable=True
            )
        if response.status_code >= 400:
            raise ProviderUnavailableError(
                f"embedding HTTP {response.status_code}", retryable=False
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderUnavailableError(
                "embedding invalid JSON", retryable=False
            ) from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or len(data) != len(body["input"]):  # type: ignore[arg-type]
            raise ProviderUnavailableError(
                "embedding response shape mismatch", retryable=False
            )
        vectors: list[list[float]] = []
        for item in data:
            embedding = item.get("embedding") if isinstance(item, dict) else None
            if (
                not isinstance(embedding, list)
                or len(embedding) != self.dimension
            ):
                raise ProviderUnavailableError(
                    "embedding dimension mismatch", retryable=False
                )
            vectors.append([float(value) for value in embedding])
        return vectors

    def start_warmup(self) -> None:
        """Hosted provider needs no warmup."""



    async def aclose(self) -> None:
        return None
