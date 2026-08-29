"""Persisted, bounded, Stage-aware Tool registry and execution harness."""

import asyncio
import json
from hashlib import sha256
from time import monotonic
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.errors import AgentError, ProviderUnavailableError
from app.core.config import Settings
from app.core.database import session_transaction
from app.harness.events import EventRecorder
from app.models.agent_run import ToolCall
from app.providers.embedding import EmbeddingProvider
from app.providers.rerank import MockRerankProvider, RerankProvider
from app.providers.search import SearchProvider
from app.schemas.enums import RunIntent
from app.tools.contracts import (
    DocumentSearchInput,
    DocumentSearchOutput,
    EvidenceItem,
    InterviewEvidenceRetrieveInput,
    InterviewEvidenceRetrieveOutput,
    MemoryLookupInput,
    MemoryLookupOutput,
    ModelToolSpec,
    RagRetrieveInput,
    RagRetrieveOutput,
    RegisteredTool,
    ResumeGapAnalyzeInput,
    ResumeGapAnalyzeOutput,
    ToolContext,
    ToolExecutionResult,
    ToolResult,
    WebSearchInput,
    WebSearchOutput,
)
from app.tools.executors import (
    DocumentSearchHandler,
    InterviewEvidenceRetrieveHandler,
    MemoryLookupHandler,
    RagRetrieveHandler,
    ResumeGapAnalyzeHandler,
    WebSearchHandler,
)


class ToolRegistry:
    def __init__(
        self,
        *,
        feature_stage: int = 3,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        max_rounds: int = 2,
        max_calls: int = 4,
        # PR-8: per-experiment allowlist. None = no-op (legacy behaviour).
        # Empty set hides every tool from the model; non-empty hides any
        # registered tool whose name is not in the set.
        available_tools_override: set[str] | None = None,
    ) -> None:
        self._feature_stage = feature_stage
        self._sessions = session_factory
        self._max_rounds = max_rounds
        self._max_calls = max_calls
        self._tools: dict[str, RegisteredTool] = {}
        self._available_tools_override = available_tools_override
        self._managed_resources: list[object] = []

    def manage_resource(self, resource: object) -> None:
        self._managed_resources.append(resource)

    async def aclose(self) -> None:
        for resource in self._managed_resources:
            close = getattr(resource, "aclose", None)
            if callable(close):
                await close()
        self._managed_resources.clear()

    def register(self, tool: RegisteredTool) -> None:
        if tool.spec.name in self._tools:
            raise ValueError(f"duplicate tool registration: {tool.spec.name}")
        self._tools[tool.spec.name] = tool

    def available_specs(
        self,
        *,
        intent: RunIntent = RunIntent.CREATE_PLAN,
        requires_fresh_information: bool = False,
    ) -> list[ModelToolSpec]:
        allowed_intent = intent in {
            RunIntent.CREATE_PLAN,
            RunIntent.REPLAN,
            RunIntent.RESUME_OPTIMIZATION,
        }
        if not allowed_intent:
            return []
        override = self._available_tools_override
        domain_tools = {"interview_evidence_retrieve", "resume_gap_analyze"}
        return [
            tool.spec
            for tool in self._tools.values()
            if tool.stage <= self._feature_stage
            and (tool.spec.name != "web_search" or requires_fresh_information)
            and (override is None or tool.spec.name in override)
            and (
                tool.spec.name in domain_tools
                if intent == RunIntent.RESUME_OPTIMIZATION
                else tool.spec.name not in domain_tools
            )
        ]

    async def execute(
        self,
        *,
        tool_name: str,
        arguments: dict[str, object],
        context: ToolContext,
        step_id: UUID,
        round_number: int,
    ) -> ToolExecutionResult:
        if self._sessions is None:
            raise RuntimeError("ToolRegistry execution requires a session factory")
        tool = self._tools.get(tool_name)
        intent_allowed = context.intent in {
            RunIntent.CREATE_PLAN,
            RunIntent.REPLAN,
            RunIntent.RESUME_OPTIMIZATION,
        }
        domain_allowed = (
            tool_name in {"interview_evidence_retrieve", "resume_gap_analyze"}
            if context.intent == RunIntent.RESUME_OPTIMIZATION
            else tool_name not in {"interview_evidence_retrieve", "resume_gap_analyze"}
        )
        fresh_allowed = tool_name != "web_search" or context.requires_fresh_information
        if (
            tool is None
            or tool.stage > self._feature_stage
            or not intent_allowed
            or not domain_allowed
            or not fresh_allowed
        ):
            return await self._record_immediate_failure(
                tool_name=tool_name,
                contract_version=tool.spec.contract_version if tool else "unknown",
                arguments=arguments,
                context=context,
                step_id=step_id,
                round_number=round_number,
                error_code="TOOL_NOT_ALLOWED",
            )
        try:
            validated_input = tool.input_model.model_validate(arguments)
        except ValidationError:
            return await self._record_immediate_failure(
                tool_name=tool_name,
                contract_version=tool.spec.contract_version,
                arguments=arguments,
                context=context,
                step_id=step_id,
                round_number=round_number,
                error_code="TOOL_ARGUMENT_INVALID",
            )
        if isinstance(validated_input, RagRetrieveInput):
            if validated_input.goal_type != context.goal_type:
                return await self._record_immediate_failure(
                    tool_name=tool_name,
                    contract_version=tool.spec.contract_version,
                    arguments=validated_input.model_dump(mode="json"),
                    context=context,
                    step_id=step_id,
                    round_number=round_number,
                    error_code="TOOL_ARGUMENT_INVALID",
                )
        args_json = validated_input.model_dump(mode="json")
        args_hash = self._hash(args_json)
        if context.replay_fixture_run_id is not None and context.fixture_only:
            fixture = await self._fixture(
                tool_name,
                tool.spec.contract_version,
                args_hash,
                context.replay_fixture_run_id,
            )
            if fixture is None:
                return await self._record_immediate_failure(
                    tool_name=tool_name,
                    contract_version=tool.spec.contract_version,
                    arguments=args_json,
                    context=context,
                    step_id=step_id,
                    round_number=round_number,
                    error_code="REPLAY_FIXTURE_MISSING",
                )
            tool_call_id = await self._record_called(
                tool=tool,
                args_json=args_json,
                args_hash=args_hash,
                context=context,
                step_id=step_id,
                round_number=round_number,
            )
            await self._record_returned(tool_call_id, fixture, monotonic(), "fixture")
            return ToolExecutionResult(
                success=True,
                result=fixture,
                tool_call_id=tool_call_id,
                reused=True,
            )
        reused = await self._reuse(tool_name, args_hash, context.run_id)
        if reused is not None:
            reused_id, reused_result = reused
            return ToolExecutionResult(
                success=True,
                result=reused_result,
                tool_call_id=reused_id,
                reused=True,
            )
        if not await self._within_budget(context.run_id, round_number):
            return await self._record_immediate_failure(
                tool_name=tool_name,
                contract_version=tool.spec.contract_version,
                arguments=args_json,
                context=context,
                step_id=step_id,
                round_number=round_number,
                error_code="TOOL_BUDGET_EXCEEDED",
            )
        tool_call_id = await self._record_called(
            tool=tool,
            args_json=args_json,
            args_hash=args_hash,
            context=context,
            step_id=step_id,
            round_number=round_number,
        )
        started = monotonic()
        timeout_seconds = min(
            tool.timeout_seconds,
            max(context.remaining_deadline_ms / 1000, 0.001),
        )
        try:
            async with asyncio.timeout(timeout_seconds):
                raw_output = await tool.handler(validated_input, context)
            output = tool.output_model.model_validate(raw_output)
            result = self._tool_result(tool, output)
            result = self._compress(result, tool.max_result_chars)
        except TimeoutError:
            return await self._record_failed(tool_call_id, tool_name, started, "TOOL_TIMEOUT")
        except ProviderUnavailableError:
            return await self._record_failed(
                tool_call_id, tool_name, started, "TOOL_PROVIDER_UNAVAILABLE"
            )
        except (AgentError, ValidationError, ValueError):
            return await self._record_failed(
                tool_call_id, tool_name, started, "TOOL_EXECUTION_FAILED"
            )
        await self._record_returned(tool_call_id, result, started, tool.provider)
        return ToolExecutionResult(success=True, result=result, tool_call_id=tool_call_id)

    async def _reuse(
        self, tool_name: str, args_hash: str, run_id: UUID
    ) -> tuple[UUID, ToolResult] | None:
        assert self._sessions is not None
        async with self._sessions() as session:
            row = await session.scalar(
                select(ToolCall)
                .where(
                    ToolCall.run_id == run_id,
                    ToolCall.tool_name == tool_name,
                    ToolCall.args_hash == args_hash,
                    ToolCall.success.is_(True),
                )
                .order_by(ToolCall.created_at)
                .limit(1)
            )
            if row is None or row.result_json is None:
                return None
            return row.id, ToolResult.model_validate(row.result_json)

    async def _fixture(
        self,
        tool_name: str,
        contract_version: str,
        args_hash: str,
        source_run_id: UUID,
    ) -> ToolResult | None:
        assert self._sessions is not None
        async with self._sessions() as session:
            row = await session.scalar(
                select(ToolCall)
                .where(
                    ToolCall.run_id == source_run_id,
                    ToolCall.tool_name == tool_name,
                    ToolCall.tool_contract_version == contract_version,
                    ToolCall.args_hash == args_hash,
                    ToolCall.success.is_(True),
                )
                .order_by(ToolCall.created_at)
                .limit(1)
            )
            if row is None or row.result_json is None:
                return None
            return ToolResult.model_validate(row.result_json)

    async def _within_budget(self, run_id: UUID, round_number: int) -> bool:
        if round_number < 1 or round_number > self._max_rounds:
            return False
        assert self._sessions is not None
        async with self._sessions() as session:
            total = await session.scalar(
                select(func.count()).select_from(ToolCall).where(ToolCall.run_id == run_id)
            )
            in_round = await session.scalar(
                select(func.count())
                .select_from(ToolCall)
                .where(ToolCall.run_id == run_id, ToolCall.round == round_number)
            )
            return (total or 0) < self._max_calls and (in_round or 0) < 2

    async def _record_called(
        self,
        *,
        tool: RegisteredTool,
        args_json: dict[str, object],
        args_hash: str,
        context: ToolContext,
        step_id: UUID,
        round_number: int,
    ) -> UUID:
        assert self._sessions is not None
        async with self._sessions() as session:
            async with session_transaction(session):
                row = ToolCall(
                    run_id=context.run_id,
                    step_id=step_id,
                    tool_name=tool.spec.name,
                    tool_contract_version=tool.spec.contract_version,
                    round=round_number,
                    args_json=args_json,
                    args_hash=args_hash,
                    provider=tool.provider,
                    success=False,
                )
                session.add(row)
                await session.flush()
                await EventRecorder(session).record(
                    context.run_id,
                    "tool.called",
                    {
                        "tool_call_id": str(row.id),
                        "tool_name": tool.spec.name,
                        "round": round_number,
                    },
                )
                return row.id

    async def _record_returned(
        self,
        tool_call_id: UUID,
        result: ToolResult,
        started: float,
        provider: str | None,
    ) -> None:
        assert self._sessions is not None
        result_json = result.model_dump(mode="json")
        result_hash = self._hash(result_json)
        async with self._sessions() as session:
            async with session_transaction(session):
                row = await session.get(ToolCall, tool_call_id, with_for_update=True)
                if row is None:
                    raise RuntimeError("ToolCall disappeared")
                row.result_json = result_json
                row.result_preview = json.dumps(result_json, ensure_ascii=False, sort_keys=True)[
                    :1000
                ]
                row.result_hash = result_hash
                row.provider = provider
                row.latency_ms = int((monotonic() - started) * 1000)
                row.success = True
                await EventRecorder(session).record(
                    row.run_id,
                    "tool.returned",
                    {
                        "tool_call_id": str(row.id),
                        "tool_name": row.tool_name,
                        "success": True,
                        "latency_ms": row.latency_ms,
                        "truncated": result.truncated,
                    },
                )

    async def _record_failed(
        self,
        tool_call_id: UUID,
        tool_name: str,
        started: float,
        error_code: str,
    ) -> ToolExecutionResult:
        result = ToolResult(tool_name=tool_name, data={"error_code": error_code})
        assert self._sessions is not None
        async with self._sessions() as session:
            async with session_transaction(session):
                row = await session.get(ToolCall, tool_call_id, with_for_update=True)
                if row is None:
                    raise RuntimeError("ToolCall disappeared")
                row.result_json = result.model_dump(mode="json")
                row.result_hash = self._hash(row.result_json)
                row.latency_ms = int((monotonic() - started) * 1000)
                row.success = False
                row.error_code = error_code
                await EventRecorder(session).record(
                    row.run_id,
                    "tool.returned",
                    {
                        "tool_call_id": str(row.id),
                        "tool_name": tool_name,
                        "success": False,
                        "latency_ms": row.latency_ms,
                        "truncated": False,
                    },
                )
        return ToolExecutionResult(success=False, result=result, error_code=error_code)

    async def _record_immediate_failure(
        self,
        *,
        tool_name: str,
        contract_version: str,
        arguments: dict[str, object],
        context: ToolContext,
        step_id: UUID,
        round_number: int,
        error_code: str,
    ) -> ToolExecutionResult:
        pseudo_tool = RegisteredTool(
            spec=ModelToolSpec(
                name=tool_name,
                description="Rejected Tool call",
                input_json_schema={},
                contract_version=contract_version,
            ),
            input_model=BaseModel,
            output_model=BaseModel,
            handler=_never_called,
        )
        row_id = await self._record_called(
            tool=pseudo_tool,
            args_json=arguments,
            args_hash=self._hash(arguments),
            context=context,
            step_id=step_id,
            round_number=round_number,
        )
        return await self._record_failed(row_id, tool_name, monotonic(), error_code)

    @staticmethod
    def _tool_result(tool: RegisteredTool, output: BaseModel) -> ToolResult:
        serialized = output.model_dump(mode="json")
        raw_evidence = serialized.pop("evidence", [])
        evidence = [EvidenceItem.model_validate(item) for item in raw_evidence]
        return ToolResult(
            tool_name=tool.spec.name,
            data=serialized,
            evidence=evidence,
            provider=tool.provider,
        )

    @staticmethod
    def _compress(result: ToolResult, max_chars: int) -> ToolResult:
        serialized = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
        if len(serialized) <= max_chars:
            return result
        data = dict(result.data)
        items = data.get("items")
        if isinstance(items, list):
            bounded_items = list(items)
            while bounded_items:
                bounded_items.pop()
                data["items"] = bounded_items
                candidate_ids = {
                    str(item.get("memory_id") or item.get("atom_id") or item.get("source_id"))
                    for item in bounded_items
                    if isinstance(item, dict)
                }
                evidence = [item for item in result.evidence if str(item.id) in candidate_ids]
                candidate = result.model_copy(
                    update={"data": data, "evidence": evidence, "truncated": True}
                )
                if (
                    len(json.dumps(candidate.model_dump(mode="json"), ensure_ascii=False))
                    <= max_chars
                ):
                    return candidate
        return result.model_copy(update={"data": {"items": []}, "evidence": [], "truncated": True})

    @staticmethod
    def _hash(value: dict[str, object]) -> str:
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return sha256(encoded).hexdigest()


async def _never_called(_: BaseModel, __: ToolContext) -> BaseModel:
    raise RuntimeError("rejected Tool handler must not execute")


def build_tool_registry(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    embedding_provider: EmbeddingProvider,
    search_provider: SearchProvider,
    rerank_provider: RerankProvider | None = None,
    available_tools_override: set[str] | None = None,
) -> ToolRegistry:
    if rerank_provider is None:
        rerank_provider = MockRerankProvider()
    registry = ToolRegistry(
        feature_stage=settings.agent_feature_stage,
        session_factory=session_factory,
        max_rounds=settings.agent_max_tool_rounds,
        max_calls=settings.agent_max_tool_calls,
        available_tools_override=available_tools_override,
    )
    registry.manage_resource(search_provider)
    registrations = [
        RegisteredTool(
            spec=ModelToolSpec(
                name="memory_lookup",
                description=(
                    "Retrieve the current user's confirmed long-term memories "
                    "(execution lessons, review findings, personal preferences). "
                    "Call this FIRST whenever the request refers to the user's "
                    "past experience, habits, or lessons learned — those facts "
                    "are ONLY visible through this tool."
                ),
                input_json_schema=MemoryLookupInput.model_json_schema(),
                contract_version="1.0",
            ),
            input_model=MemoryLookupInput,
            output_model=MemoryLookupOutput,
            handler=MemoryLookupHandler(session_factory, embedding_provider),
            timeout_seconds=settings.tool_timeout_seconds,
            provider=embedding_provider.provider_name,
        ),
        RegisteredTool(
            spec=ModelToolSpec(
                name="rag_retrieve",
                description="Retrieve curated career experience for the current goal type.",
                input_json_schema=RagRetrieveInput.model_json_schema(),
                contract_version="1.0",
            ),
            input_model=RagRetrieveInput,
            output_model=RagRetrieveOutput,
            handler=RagRetrieveHandler(
                session_factory, embedding_provider, settings.rag_min_similarity
            ),
            timeout_seconds=settings.tool_timeout_seconds,
            provider=embedding_provider.provider_name,
        ),
        RegisteredTool(
            spec=ModelToolSpec(
                name="document_search",
                description=(
                    "Hybrid search over the user's own resume and target-JD "
                    "documents (semantic + lexical, reranked, gated)."
                ),
                input_json_schema=DocumentSearchInput.model_json_schema(),
                contract_version="1.0",
            ),
            input_model=DocumentSearchInput,
            output_model=DocumentSearchOutput,
            handler=DocumentSearchHandler(
                session_factory,
                embedding_provider,
                rerank_provider,
                settings.rag_min_rerank_score,
            ),
            timeout_seconds=settings.tool_timeout_seconds,
            provider=rerank_provider.provider_name,
        ),
        RegisteredTool(
            spec=ModelToolSpec(
                name="web_search",
                description="Search external evidence only when fresh information is required.",
                input_json_schema=WebSearchInput.model_json_schema(),
                contract_version="1.0",
            ),
            input_model=WebSearchInput,
            output_model=WebSearchOutput,
            handler=WebSearchHandler(session_factory, search_provider),
            timeout_seconds=settings.tool_timeout_seconds,
            provider=search_provider.provider_name,
        ),
        RegisteredTool(
            spec=ModelToolSpec(
                name="interview_evidence_retrieve",
                description=(
                    "Retrieve evidence-bearing answers from the selected completed interview."
                ),
                input_json_schema=InterviewEvidenceRetrieveInput.model_json_schema(),
                contract_version="2.0",
            ),
            input_model=InterviewEvidenceRetrieveInput,
            output_model=InterviewEvidenceRetrieveOutput,
            handler=InterviewEvidenceRetrieveHandler(session_factory),
            timeout_seconds=settings.tool_timeout_seconds,
            provider="local",
            max_result_chars=32000,
        ),
        RegisteredTool(
            spec=ModelToolSpec(
                name="resume_gap_analyze",
                description="Analyze Resume claim coverage against the frozen target JD.",
                input_json_schema=ResumeGapAnalyzeInput.model_json_schema(),
                contract_version="2.0",
            ),
            input_model=ResumeGapAnalyzeInput,
            output_model=ResumeGapAnalyzeOutput,
            handler=ResumeGapAnalyzeHandler(session_factory),
            timeout_seconds=settings.tool_timeout_seconds,
            provider="local",
            max_result_chars=32000,
        ),
    ]
    for registration in registrations:
        registry.register(registration)
    return registry
