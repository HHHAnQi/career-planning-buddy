"""FixtureProvider wrappers: lazy-record + replay on top of seed providers.

Each wrapper is a drop-in replacement for the inner provider (Mock or live)
that ALSO drives the audit row via ``ProviderCallRecorder`` and consults
``FixtureStore`` for record / replay. The recorder-sharing keeps the
run-global ``sequence`` monotonic across LLM/Search/Embedding identical to
the AuditProvider path -- the inner audit + the fixture layer are layered,
not bypassed.

The wrappers stay transparent: they return whatever the inner provider
returned, regardless of record-vs-replay. The recorded fixture's
``response_projection`` mirrors what the recorder would have stored
independently, so audit rows + fixture items are consistent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic_core import to_jsonable_python

from app.harness.provider_calls.fixture_store import (
    FixtureDesyncError,
    FixtureStore,
)
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
    from app.providers.search import SearchProvider


class _BaseFixtureProvider:
    """Shared record-vs-replay decision used by every FixtureXxx wrapper."""

    def __init__(
        self,
        *,
        recorder: ProviderCallRecorder,
        store: FixtureStore,
        provider_kind: str,
    ) -> None:
        self._recorder = recorder
        self._store = store
        self._provider_kind = provider_kind
        self._attempt_counters: dict[str, int] = {}

    def _retry_attempt(self, method: str) -> int:
        if method in {"repair_format", "repair_business_rules"}:
            self._attempt_counters[method] = (
                self._attempt_counters.get(method, 0) + 1
            )
        return self._attempt_counters.get(method, 0)

    async def _invoke(
        self,
        *,
        provider_method: str,
        retry_attempt: int,
        request_projection: dict[str, object],
        respond_projection: Any,
        call_factory: Any,
    ) -> Any:
        """Persist the audit row + record/replay the fixture. Returns the raw
        provider response on record path, or the recorded response_projection
        (a dict) on replay path. Callers ``isinstance``-narrow before use.
        """

        # Replay path: short-circuit the inner call, persist the audit row
        # with the recorded response projection, and return a rehydrated raw
        # response from the fixture entry.
        if self._store.is_replay():
            sequence, logical = self._recorder.allocate_sequence(
                provider_kind=self._provider_kind,
                provider_method=provider_method,
                retry_attempt=retry_attempt,
            )
            entry = self._store.consume(
                sequence=sequence,
                provider_kind=self._provider_kind,
                provider_method=provider_method,
                retry_attempt=retry_attempt,
                request_projection=request_projection,
            )
            usage = (
                entry.response_payload.get("usage", {})
                if isinstance(entry.response_payload, dict)
                else {}
            )
            tokens_in = usage.get("tokens_in") if isinstance(usage, dict) else None
            tokens_out = usage.get("tokens_out") if isinstance(usage, dict) else None
            model_id = usage.get("model_id") if isinstance(usage, dict) else None
            await self._recorder._persist(  # noqa: SLF001
                sequence=sequence,
                provider_kind=self._provider_kind,
                provider_method=provider_method,
                logical=logical,
                retry_attempt=retry_attempt,
                request_projection=request_projection,
                request_hash=entry.request_projection_hash,
                response_proj=entry.response_projection,
                response_hash=entry.response_projection_hash,
                status="ok",
                error_code=None,
                tokens_in=tokens_in if isinstance(tokens_in, int) else None,
                tokens_out=tokens_out if isinstance(tokens_out, int) else None,
                latency_ms=0,
                model_id=model_id if isinstance(model_id, str) else self._recorder.model_id,
            )
            return entry.response_payload

        # Record path: invoke the real provider, write audit + raw fixture.
        raw_holder: dict[str, object] = {}

        async def _call_with_capture() -> Any:
            raw = await call_factory()
            raw_holder["raw"] = raw
            return raw

        result = await self._recorder.invoke(
            provider_kind=self._provider_kind,
            provider_method=provider_method,
            retry_attempt=retry_attempt,
            request_projection=request_projection,
            coro_factory=_call_with_capture,
            respond_projection=respond_projection,
        )
        # Record the fixture entry (response_projection comes from result).
        if result.response_projection is not None:
            self._store.record(
                sequence=result.sequence,
                provider_kind=self._provider_kind,
                provider_method=provider_method,
                retry_attempt=retry_attempt,
                request_projection=request_projection,
                response_projection=result.response_projection,
                response_payload=to_jsonable_python(raw_holder.get("raw")),
            )
        return raw_holder.get("raw")


class FixturePlanningProvider(_BaseFixtureProvider):
    provider_name = "fixture-llm"

    def __init__(
        self,
        inner: PlanningProvider,
        *,
        recorder: ProviderCallRecorder,
        store: FixtureStore,
    ) -> None:
        super().__init__(
            recorder=recorder, store=store, provider_kind="llm"
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
        request = request_generate_agent_turn(
            message=message,
            context=context,
            replan_mode=replan_mode,
            available_tools=available_tools,
            evidence_catalog=evidence_catalog,
            force_final=force_final,
        )
        result = await self._invoke(
            provider_method="generate_agent_turn",
            retry_attempt=self._retry_attempt("generate_agent_turn"),
            request_projection=request,
            respond_projection=response_llm_turn,
            call_factory=lambda: self._inner.generate_agent_turn(
                message=message,
                context=context,
                replan_mode=replan_mode,
                available_tools=available_tools,
                evidence_catalog=evidence_catalog,
                force_final=force_final,
            ),
        )
        return result if isinstance(result, dict) else {}

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
        result = await self._invoke(
            provider_method="generate_plan",
            retry_attempt=self._retry_attempt("generate_plan"),
            request_projection=request,
            respond_projection=response_llm_turn,
            call_factory=lambda: self._inner.generate_plan(
                message=message,
                context=context,
                replan_mode=replan_mode,
                evidence_catalog=evidence_catalog,
            ),
        )
        return result if isinstance(result, dict) else {}

    async def repair_format(
        self,
        *,
        raw_output: Mapping[str, object],
        context: PlanningContext,
        replan_mode: ReplanMode,
        evidence_catalog: list[EvidenceCatalogItem],
    ) -> Mapping[str, object]:
        request = request_repair(
            method="repair_format",
            raw_output=None,
            candidate=None,
            repair_instructions="",
            attempt=self._retry_attempt("repair_format"),
            raw_output_proj=dict(raw_output),
        )
        result = await self._invoke(
            provider_method="repair_format",
            retry_attempt=self._attempt_counters.get("repair_format", 0),
            request_projection=request,
            respond_projection=response_llm_turn,
            call_factory=lambda: self._inner.repair_format(
                raw_output=raw_output,
                context=context,
                replan_mode=replan_mode,
                evidence_catalog=evidence_catalog,
            ),
        )
        return result if isinstance(result, dict) else {}

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
        request = request_repair(
            method="repair_business_rules",
            raw_output=None,
            candidate=candidate,
            repair_instructions=repr(repair_instructions),
            attempt=self._retry_attempt("repair_business_rules"),
        )
        result = await self._invoke(
            provider_method="repair_business_rules",
            retry_attempt=self._attempt_counters.get(
                "repair_business_rules", 0
            ),
            request_projection=request,
            respond_projection=response_llm_turn,
            call_factory=lambda: self._inner.repair_business_rules(
                candidate=candidate,
                context=context,
                repair_instructions=repair_instructions,
                message=message,
                replan_mode=replan_mode,
                evidence_catalog=evidence_catalog,
            ),
        )
        return result if isinstance(result, dict) else {}


class FixtureSearchProvider(_BaseFixtureProvider):
    provider_name = "fixture-search"

    def __init__(
        self,
        inner: SearchProvider,
        *,
        recorder: ProviderCallRecorder,
        store: FixtureStore,
    ) -> None:
        super().__init__(
            recorder=recorder, store=store, provider_kind="search"
        )
        self._inner = inner

    async def search(
        self,
        *,
        query: str,
        limit: int,
        freshness_days: int | None,
    ) -> list[object]:
        request = request_search(
            query=query, limit=limit, freshness_days=freshness_days,
        )
        result = await self._invoke(
            provider_method="search",
            retry_attempt=0,
            request_projection=request,
            respond_projection=response_search,
            call_factory=lambda: self._inner.search(
                query=query, limit=limit, freshness_days=freshness_days,
            ),
        )
        return result if isinstance(result, list) else []


class FixtureEmbeddingProvider(_BaseFixtureProvider):
    provider_name = "fixture-embedding"

    def __init__(
        self,
        inner: EmbeddingProvider,
        *,
        recorder: ProviderCallRecorder,
        store: FixtureStore,
    ) -> None:
        super().__init__(
            recorder=recorder, store=store, provider_kind="embedding"
        )
        self._inner = inner

    @property
    def dimension(self) -> int:
        return self._inner.dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        request = request_embedding(texts=texts)
        result = await self._invoke(
            provider_method="embed",
            retry_attempt=0,
            request_projection=request,
            respond_projection=lambda r: response_embedding(
                r, dimension_hint=self._inner.dimension
            ),
            call_factory=lambda: self._inner.embed(texts=texts),
        )
        return result if isinstance(result, list) else []


__all__ = [
    "FixtureDesyncError",
    "FixtureEmbeddingProvider",
    "FixturePlanningProvider",
    "FixtureSearchProvider",
]
