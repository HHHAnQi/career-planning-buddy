"""Eval-only bounded retry, backoff, pacing, and concurrency wrappers."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from time import monotonic
from typing import TYPE_CHECKING, TypeVar

from app.agent.errors import (
    AgentError,
    ProviderRateLimitError,
    ProviderRetriesExhaustedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.schemas.agent_runs import EvidenceCatalogItem, PlanCandidate, PlanningContext
from app.schemas.enums import ReplanMode
from app.tools.contracts import ModelToolSpec

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.providers.embedding import EmbeddingProvider
    from app.providers.llm import PlanningProvider
    from app.providers.search import SearchProvider, SearchResultItem

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class LiveEvalRetryPolicy:
    max_attempts: int
    base_seconds: float
    max_seconds: float
    jitter_ratio: float = 0.25

    def delay_seconds(
        self,
        *,
        retry_index: int,
        error: AgentError,
        random_value: float,
    ) -> float:
        retry_after = error.retry_after_seconds
        if retry_after is not None:
            return min(self.max_seconds, max(0.0, retry_after))
        exponential = self.base_seconds * float(2**retry_index)
        jitter = exponential * self.jitter_ratio * max(0.0, min(1.0, random_value))
        return float(min(self.max_seconds, exponential + jitter))


class RetryAttemptState:
    """Task-local physical attempt index consumed by the Audit wrapper."""

    def __init__(self) -> None:
        self._attempt: ContextVar[int] = ContextVar("eval_retry_attempt", default=0)

    def get(self) -> int:
        return self._attempt.get()

    def set(self, attempt: int) -> Token[int]:
        return self._attempt.set(attempt)

    def reset(self, token: Token[int]) -> None:
        self._attempt.reset(token)


class LiveEvalCallController:
    """Experiment-local physical-call concurrency and start pacing gate."""

    def __init__(self, *, concurrency: int, pacing_seconds: float) -> None:
        self._semaphore = asyncio.Semaphore(concurrency)
        self._pacing_seconds = pacing_seconds
        self._pacing_lock = asyncio.Lock()
        self._next_start = 0.0

    @asynccontextmanager
    async def slot(self, *, deadline: float) -> AsyncIterator[None]:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise ProviderTimeoutError(
                "Eval live provider deadline exhausted", retryable=False
            )
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=remaining)
        except TimeoutError as exc:
            raise ProviderTimeoutError(
                "Eval live provider concurrency wait exceeded deadline",
                retryable=False,
            ) from exc
        try:
            async with self._pacing_lock:
                delay = max(0.0, self._next_start - monotonic())
                if delay >= deadline - monotonic():
                    raise ProviderTimeoutError(
                        "Eval live provider pacing exceeded deadline",
                        retryable=False,
                    )
                if delay:
                    await asyncio.sleep(delay)
                self._next_start = monotonic() + self._pacing_seconds
            yield
        finally:
            self._semaphore.release()


class _RetryingProvider:
    def __init__(
        self,
        *,
        controller: LiveEvalCallController,
        policy: LiveEvalRetryPolicy,
        deadline_seconds: float,
        attempt_state: RetryAttemptState,
        random_source: Callable[[], float] = random.random,
    ) -> None:
        self._controller = controller
        self._policy = policy
        self._deadline_seconds = deadline_seconds
        self._attempt_state = attempt_state
        self._random_source = random_source

    async def _invoke(self, call: Callable[[], Awaitable[T]]) -> T:
        deadline = monotonic() + self._deadline_seconds
        for attempt in range(self._policy.max_attempts):
            token = self._attempt_state.set(attempt)
            try:
                async with self._controller.slot(deadline=deadline):
                    return await call()
            except asyncio.CancelledError:
                raise
            except AgentError as exc:
                if not _is_transient(exc):
                    raise
                if attempt + 1 >= self._policy.max_attempts:
                    raise ProviderRetriesExhaustedError(
                        f"Eval live provider exhausted after {attempt + 1} attempts",
                        retryable=False,
                    ) from exc
                delay = self._policy.delay_seconds(
                    retry_index=attempt,
                    error=exc,
                    random_value=self._random_source(),
                )
                if delay >= deadline - monotonic():
                    raise ProviderRetriesExhaustedError(
                        "Eval live provider retry deadline exhausted",
                        retryable=False,
                    ) from exc
                await asyncio.sleep(delay)
            finally:
                self._attempt_state.reset(token)
        raise AssertionError("retry loop must return or raise")


def _is_transient(error: AgentError) -> bool:
    if error.retryable is False:
        return False
    return isinstance(
        error,
        (ProviderRateLimitError, ProviderTimeoutError, ProviderUnavailableError),
    )


class RetryingPlanningProvider(_RetryingProvider):
    provider_name = "eval-live-retry-llm"

    def __init__(
        self,
        inner: PlanningProvider,
        *,
        controller: LiveEvalCallController,
        policy: LiveEvalRetryPolicy,
        deadline_seconds: float,
        attempt_state: RetryAttemptState,
        random_source: Callable[[], float] = random.random,
    ) -> None:
        super().__init__(
            controller=controller,
            policy=policy,
            deadline_seconds=deadline_seconds,
            attempt_state=attempt_state,
            random_source=random_source,
        )
        self._inner = inner

    async def generate_agent_turn(
        self,
        *,
        message: str,
        context: PlanningContext,
        replan_mode: ReplanMode,
        available_tools: list[ModelToolSpec],
        evidence_catalog: list[EvidenceCatalogItem],
        force_final: bool,
    ) -> Mapping[str, object]:
        return await self._invoke(
            lambda: self._inner.generate_agent_turn(
                message=message,
                context=context,
                replan_mode=replan_mode,
                available_tools=available_tools,
                evidence_catalog=evidence_catalog,
                force_final=force_final,
            )
        )

    async def generate_plan(
        self,
        *,
        message: str,
        context: PlanningContext,
        replan_mode: ReplanMode,
        evidence_catalog: list[EvidenceCatalogItem],
    ) -> Mapping[str, object]:
        return await self._invoke(
            lambda: self._inner.generate_plan(
                message=message,
                context=context,
                replan_mode=replan_mode,
                evidence_catalog=evidence_catalog,
            )
        )

    async def repair_format(
        self,
        *,
        raw_output: Mapping[str, object],
        context: PlanningContext,
        replan_mode: ReplanMode,
        evidence_catalog: list[EvidenceCatalogItem],
    ) -> Mapping[str, object]:
        return await self._invoke(
            lambda: self._inner.repair_format(
                raw_output=raw_output,
                context=context,
                replan_mode=replan_mode,
                evidence_catalog=evidence_catalog,
            )
        )

    async def repair_business_rules(
        self,
        *,
        candidate: PlanCandidate,
        context: PlanningContext,
        repair_instructions: list[str],
        message: str,
        replan_mode: ReplanMode,
        evidence_catalog: list[EvidenceCatalogItem],
    ) -> Mapping[str, object]:
        return await self._invoke(
            lambda: self._inner.repair_business_rules(
                candidate=candidate,
                context=context,
                repair_instructions=repair_instructions,
                message=message,
                replan_mode=replan_mode,
                evidence_catalog=evidence_catalog,
            )
        )


class RetryingSearchProvider(_RetryingProvider):
    provider_name = "eval-live-retry-search"

    def __init__(
        self,
        inner: SearchProvider,
        *,
        controller: LiveEvalCallController,
        policy: LiveEvalRetryPolicy,
        deadline_seconds: float,
        attempt_state: RetryAttemptState,
        random_source: Callable[[], float] = random.random,
    ) -> None:
        super().__init__(
            controller=controller,
            policy=policy,
            deadline_seconds=deadline_seconds,
            attempt_state=attempt_state,
            random_source=random_source,
        )
        self._inner = inner

    async def search(
        self, *, query: str, limit: int, freshness_days: int | None
    ) -> list[SearchResultItem]:
        return await self._invoke(
            lambda: self._inner.search(
                query=query, limit=limit, freshness_days=freshness_days
            )
        )


class RetryingEmbeddingProvider(_RetryingProvider):
    provider_name = "eval-live-retry-embedding"

    def __init__(
        self,
        inner: EmbeddingProvider,
        *,
        controller: LiveEvalCallController,
        policy: LiveEvalRetryPolicy,
        deadline_seconds: float,
        attempt_state: RetryAttemptState,
        random_source: Callable[[], float] = random.random,
    ) -> None:
        super().__init__(
            controller=controller,
            policy=policy,
            deadline_seconds=deadline_seconds,
            attempt_state=attempt_state,
            random_source=random_source,
        )
        self._inner = inner

    @property
    def dimension(self) -> int:
        return self._inner.dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await self._invoke(lambda: self._inner.embed(texts))


__all__ = [
    "LiveEvalCallController",
    "LiveEvalRetryPolicy",
    "RetryAttemptState",
    "RetryingEmbeddingProvider",
    "RetryingPlanningProvider",
    "RetryingSearchProvider",
]
