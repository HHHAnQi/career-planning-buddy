"""Local-only and deterministic Mock embedding Provider adapters."""

import asyncio
from hashlib import sha256
from importlib import import_module
from math import sqrt
from pathlib import Path
from typing import Protocol, cast

from app.agent.errors import ProviderConfigurationError, ProviderUnavailableError
from app.core.config import Settings


class EmbeddingProvider(Protocol):
    provider_name: str
    dimension: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class MockEmbeddingProvider:
    """Stable hash-derived vectors suitable for tests and offline CI."""

    provider_name = "mock"

    def __init__(self, dimension: int = 1024) -> None:
        self.dimension = dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if any("[mock:embedding-error]" in text for text in texts):
            raise ProviderUnavailableError("Mock Embedding Provider unavailable")
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        seed = sha256(text.encode("utf-8")).digest()
        values = [((seed[index % len(seed)] / 255) * 2) - 1 for index in range(self.dimension)]
        magnitude = sqrt(sum(value * value for value in values)) or 1
        return [value / magnitude for value in values]


class _EncodedArray(Protocol):
    def tolist(self) -> object: ...


class _SentenceTransformerModel(Protocol):
    def encode(
        self,
        sentences: list[str],
        *,
        normalize_embeddings: bool,
        show_progress_bar: bool,
    ) -> _EncodedArray: ...


class LocalEmbeddingProvider:
    """Lazy SentenceTransformer adapter that never downloads missing weights."""

    provider_name = "local"

    def __init__(self, model_path: Path, *, dimension: int, model_name: str | None) -> None:
        self._model_path = model_path.resolve()
        self.dimension = dimension
        self.model_name = model_name or self._model_path.name
        self._model: _SentenceTransformerModel | None = None
        self._lock = asyncio.Lock()
        self._warmup_task: asyncio.Task[None] | None = None

    def start_warmup(self) -> asyncio.Task[None]:
        """Begin one process-local model load without blocking application startup."""
        if self._warmup_task is None:
            self._warmup_task = asyncio.create_task(
                self.warmup(),
                name="local-embedding-warmup",
            )
        return self._warmup_task

    async def warmup(self) -> None:
        await self._load()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self._warmup_task is not None and not self._warmup_task.done():
            raise ProviderUnavailableError(
                "local embedding is warming up; semantic retrieval is temporarily unavailable"
            )
        if self._warmup_task is not None:
            await self._warmup_task
        model = await self._load()
        encoded = await asyncio.to_thread(
            model.encode,
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        raw = encoded.tolist()
        if not isinstance(raw, list):
            raise ProviderUnavailableError(
                "local embedding returned an invalid array", retryable=False
            )
        vectors: list[list[float]] = []
        for row in raw:
            if not isinstance(row, list) or len(row) != self.dimension:
                raise ProviderConfigurationError(
                    "local embedding dimension does not match EMBEDDING_DIM"
                )
            vectors.append([float(value) for value in row])
        return vectors

    async def _load(self) -> _SentenceTransformerModel:
        if self._model is not None:
            return self._model
        async with self._lock:
            if self._model is not None:
                return self._model
            try:
                module = import_module("sentence_transformers")
                model_class = module.__dict__.get("SentenceTransformer")
                if model_class is None:
                    raise ImportError("sentence_transformers has no SentenceTransformer")
                model_object = await asyncio.to_thread(
                    model_class,
                    str(self._model_path),
                    local_files_only=True,
                )
            except (ImportError, OSError, RuntimeError) as exc:
                raise ProviderConfigurationError(
                    "local embedding requires sentence-transformers and complete local weights"
                ) from exc
            self._model = cast(_SentenceTransformerModel, model_object)
            return self._model


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "mock":
        return MockEmbeddingProvider(settings.embedding_dim)
    if settings.embedding_provider == "openai_compatible":
        if (
            settings.embedding_api_key is None
            or settings.embedding_base_url is None
            or settings.embedding_api_model is None
        ):
            raise ProviderConfigurationError(
                "openai_compatible embedding requires EMBEDDING_API_KEY, "
                "EMBEDDING_BASE_URL, and EMBEDDING_API_MODEL"
            )
        from app.providers.embedding_api import OpenAICompatibleEmbeddingProvider

        return OpenAICompatibleEmbeddingProvider(
            api_key=settings.embedding_api_key.get_secret_value(),
            base_url=str(settings.embedding_base_url),
            model=settings.embedding_api_model,
            dimension=settings.embedding_dim,
        )
    if settings.embedding_model_path is None:
        raise ProviderConfigurationError("local embedding requires EMBEDDING_MODEL_PATH")
    return LocalEmbeddingProvider(
        settings.embedding_model_path,
        dimension=settings.embedding_dim,
        model_name=settings.embedding_model_name,
    )
