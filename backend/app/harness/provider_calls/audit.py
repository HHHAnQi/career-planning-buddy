"""Auditing wrappers: record every Provider call without altering semantics.

Three classes mirror the protocols we need to cover:

* ``AuditPlanningProvider`` wraps any ``PlanningProvider`` (LLM planning:
  ``generate_agent_turn`` / ``generate_plan`` / ``repair_format`` /
  ``repair_business_rules``).
* ``AuditSearchProvider`` wraps any ``SearchProvider`` (``search``).
* ``AuditEmbeddingProvider`` wraps any ``EmbeddingProvider`` (``embed``).

Every method produces a structured + redacted request projection upstream
of the call, then forwards the raw response into a deterministic response
projection. The recorders persist a row through their own session, so the
graph's in-flight transaction is unaffected.

The wrappers are *transparent*: each method returns whatever the inner
provider returned. The ``raw_holder`` pattern captures the raw response
into a closure so we can hand it back without re-invoking the (potentially
expensive or non-idempotent) inner call.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from app.harness.provider_calls.projections import (
    request_embedding,
    request_generate_agent_turn,
    request_generate_plan,
    request_repair,
    request_search,
    response_embedding,
    response_llm_turn,
    response_search,
)
from app.harness.provider_calls.recorder import ProviderCallRecorder
from app.providers.llm import PlanningProvider
from app.schemas.agent_runs import (
    EvidenceCatalogItem,
    PlanCandidate,
    PlanningContext,
)
from app.schemas.enums import ReplanMode
from app.tools.contracts import ModelToolSpec

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.providers.embedding import EmbeddingProvider
    from app.providers.search import SearchProvider, SearchResultItem


class AuditPlanningProvider:
    """Wrap a ``PlanningProvider`` to audit each call."""

    provider_name = "audit-llm"

    def __init__(
        self,
        inner: PlanningProvider,
        recorder: ProviderCallRecorder,
        *,
        attempt_counters: dict[str, int] | None = None,
        retry_attempt_getter: Callable[[], int] | None = None,
    ) -> None:
        self._inner = inner
        self._recorder = recorder
        self._attempt_counters = attempt_counters or {}
        self._retry_attempt_getter = retry_attempt_getter

    def _retry_attempt(self, method: str) -> int:
        if self._retry_attempt_getter is not None:
            return self._retry_attempt_getter()
        if method in {"repair_format", "repair_business_rules"}:
            self._attempt_counters[method] = (
                self._attempt_counters.get(method, 0) + 1
            )
        return self._attempt_counters.get(method, 0)

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
        request = request_generate_agent_turn(
            message=message,
            context=context,
            replan_mode=replan_mode,
            available_tools=available_tools,
            evidence_catalog=evidence_catalog,
            force_final=force_final,
        )
        raw_holder: dict[str, object] = {}

        async def _call() -> Mapping[str, object]:
            raw = await self._inner.generate_agent_turn(
                message=message,
                context=context,
                replan_mode=replan_mode,
                available_tools=available_tools,
                evidence_catalog=evidence_catalog,
                force_final=force_final,
            )
            raw_holder["raw"] = raw
            return raw

        await self._recorder.invoke(
            provider_kind="llm",
            provider_method="generate_agent_turn",
            retry_attempt=self._retry_attempt("generate_agent_turn"),
            request_projection=request,
            coro_factory=_call,
            respond_projection=response_llm_turn,
        )
        return cast("Mapping[str, object]", raw_holder.get("raw", {}))

    async def generate_plan(
        self,
        *,
        message: str,
        context: PlanningContext,
        replan_mode: ReplanMode,
        evidence_catalog: list[EvidenceCatalogItem],
    ) -> Mapping[str, object]:
        request = request_generate_plan(
            message=message,
            context=context,
            replan_mode=replan_mode,
        )
        raw_holder: dict[str, object] = {}

        async def _call() -> Mapping[str, object]:
            raw = await self._inner.generate_plan(
                message=message,
                context=context,
                replan_mode=replan_mode,
                evidence_catalog=evidence_catalog,
            )
            raw_holder["raw"] = raw
            return raw

        await self._recorder.invoke(
            provider_kind="llm",
            provider_method="generate_plan",
            retry_attempt=self._retry_attempt("generate_plan"),
            request_projection=request,
            coro_factory=_call,
            respond_projection=response_llm_turn,
        )
        return cast("Mapping[str, object]", raw_holder.get("raw", {}))

    async def repair_format(
        self,
        *,
        raw_output: Mapping[str, object],
        context: PlanningContext,
        replan_mode: ReplanMode,
        evidence_catalog: list[EvidenceCatalogItem],
    ) -> Mapping[str, object]:
        retry_attempt = self._retry_attempt("repair_format")
        request = request_repair(
            method="repair_format",
            raw_output=None,
            candidate=None,
            repair_instructions="",
            attempt=retry_attempt,
            raw_output_proj=dict(raw_output),
        )
        raw_holder: dict[str, object] = {}

        async def _call() -> Mapping[str, object]:
            raw = await self._inner.repair_format(
                raw_output=raw_output,
                context=context,
                replan_mode=replan_mode,
                evidence_catalog=evidence_catalog,
            )
            raw_holder["raw"] = raw
            return raw

        await self._recorder.invoke(
            provider_kind="llm",
            provider_method="repair_format",
            retry_attempt=retry_attempt,
            request_projection=request,
            coro_factory=_call,
            respond_projection=response_llm_turn,
        )
        return cast("Mapping[str, object]", raw_holder.get("raw", {}))

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
        retry_attempt = self._retry_attempt("repair_business_rules")
        request = request_repair(
            method="repair_business_rules",
            raw_output=None,
            candidate=candidate,
            repair_instructions=repr(repair_instructions),
            attempt=retry_attempt,
        )
        raw_holder: dict[str, object] = {}

        async def _call() -> Mapping[str, object]:
            raw = await self._inner.repair_business_rules(
                candidate=candidate,
                context=context,
                repair_instructions=repair_instructions,
                message=message,
                replan_mode=replan_mode,
                evidence_catalog=evidence_catalog,
            )
            raw_holder["raw"] = raw
            return raw

        await self._recorder.invoke(
            provider_kind="llm",
            provider_method="repair_business_rules",
            retry_attempt=retry_attempt,
            request_projection=request,
            coro_factory=_call,
            respond_projection=response_llm_turn,
        )
        return cast("Mapping[str, object]", raw_holder.get("raw", {}))


class AuditSearchProvider:
    """Wrap a ``SearchProvider`` to audit each call."""

    provider_name = "audit-search"

    def __init__(
        self,
        inner: SearchProvider,
        recorder: ProviderCallRecorder,
        *,
        retry_attempt_getter: Callable[[], int] | None = None,
    ) -> None:
        self._inner = inner
        self._recorder = recorder
        self._retry_attempt_getter = retry_attempt_getter

    async def search(
        self,
        *,
        query: str,
        limit: int,
        freshness_days: int | None,
    ) -> list[SearchResultItem]:
        request = request_search(
            query=query, limit=limit, freshness_days=freshness_days,
        )
        raw_holder: dict[str, list[SearchResultItem]] = {}

        async def _call() -> list[SearchResultItem]:
            raw = await self._inner.search(
                query=query, limit=limit, freshness_days=freshness_days,
            )
            raw_holder["raw"] = raw
            return raw

        await self._recorder.invoke(
            provider_kind="search",
            provider_method="search",
            retry_attempt=(
                self._retry_attempt_getter()
                if self._retry_attempt_getter is not None
                else 0
            ),
            request_projection=request,
            coro_factory=_call,
            respond_projection=response_search,
        )
        return raw_holder.get("raw", [])


class AuditEmbeddingProvider:
    """Wrap an ``EmbeddingProvider`` to audit each call."""

    provider_name = "audit-embedding"

    def __init__(
        self,
        inner: EmbeddingProvider,
        recorder: ProviderCallRecorder,
        *,
        retry_attempt_getter: Callable[[], int] | None = None,
    ) -> None:
        self._inner = inner
        self._recorder = recorder
        self._retry_attempt_getter = retry_attempt_getter

    @property
    def dimension(self) -> int:
        return self._inner.dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        request = request_embedding(texts=texts)
        raw_holder: dict[str, object] = {}

        async def _call() -> list[list[float]]:
            raw = await self._inner.embed(texts=texts)
            raw_holder["raw"] = raw
            return raw

        await self._recorder.invoke(
            provider_kind="embedding",
            provider_method="embed",
            retry_attempt=(
                self._retry_attempt_getter()
                if self._retry_attempt_getter is not None
                else 0
            ),
            request_projection=request,
            coro_factory=_call,
            respond_projection=lambda r: response_embedding(
                r, dimension_hint=self._inner.dimension
            ),
        )
        return cast("list[list[float]]", raw_holder.get("raw", []))


__all__ = [
    "AuditEmbeddingProvider",
    "AuditPlanningProvider",
    "AuditSearchProvider",
]
