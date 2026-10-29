"""Bounded provider and Tool loop for producing one planning candidate."""

import asyncio
from collections.abc import Mapping, Sequence
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.errors import StructuredOutputError
from app.agent.node_runner import NodeOutput, NodeTelemetry
from app.agent.nodes import fallback_candidate
from app.agent.runtime_support import (
    build_provider_telemetry,
    combine_provider_usage,
    extract_provider_usage,
)
from app.harness.budget import BudgetGuard
from app.harness.evidence import build_evidence_visibility
from app.harness.stream_progress import StreamProgressPublisher
from app.providers.llm import PlanningProvider
from app.providers.streaming import bind_stream_delta_sink
from app.schemas.agent_runs import (
    AgentTurnResponse,
    EvidenceCatalogItem,
    EvidenceVisibility,
    PlanCandidate,
    PlanningState,
    ProviderPlanResponse,
    ProviderToolCall,
    ProviderUsage,
)
from app.schemas.enums import RunIntent
from app.tools.contracts import EvidenceItem, ToolContext
from app.tools.registry import ToolRegistry

CandidateResult = tuple[
    PlanCandidate,
    list[EvidenceCatalogItem],
    EvidenceVisibility,
    int,
    int,
]


class CareerPlanningAgentRunner:
    """Execute the only autonomous Agent loop under explicit budgets."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        provider: PlanningProvider,
        budget: BudgetGuard,
        tool_registry: ToolRegistry,
    ) -> None:
        self._session_factory = session_factory
        self._provider = provider
        self._budget = budget
        self._tool_registry = tool_registry

    async def generate(
        self,
        state: PlanningState,
        step_id: UUID,
    ) -> NodeOutput[CandidateResult]:
        context = state["planning_context"]
        mode = state["intent"].replan_mode
        evidence_catalog = list(state.get("evidence_catalog", []))
        tool_round = state.get("tool_round", 0)
        tool_call_count = state.get("tool_call_count", 0)
        total_usage: ProviderUsage | None = None
        stream_summaries: list[dict[str, object]] = []
        prompt_version = state["runtime_config"].prompt_versions["career_planning"]

        def step_telemetry(visibility: EvidenceVisibility) -> NodeTelemetry:
            assert total_usage is not None
            telemetry = build_provider_telemetry(total_usage, prompt_version, visibility)
            if stream_summaries:
                telemetry.trace_data["llm_stream"] = stream_summaries
            return telemetry

        for turn_index in range(3):
            force_final = (
                tool_round >= state["runtime_config"].max_tool_rounds
                or tool_call_count >= state["runtime_config"].max_tool_calls
            )
            available_tools = self._tool_registry.available_specs(
                intent=state["intent"].intent,
                requires_fresh_information=state["intent"].requires_fresh_information,
            )
            if (
                turn_index == 0
                and not force_final
                and not state["runtime_config"].memory_disabled
                and state["intent"].references_past_context
                and state["intent"].intent in {RunIntent.CREATE_PLAN, RunIntent.REPLAN}
                and not any(item.kind == "memory" for item in evidence_catalog)
            ):
                tool_call_count = await self._preload_memory(
                    state=state,
                    step_id=step_id,
                    evidence_catalog=evidence_catalog,
                    tool_call_count=tool_call_count,
                )
            if force_final:
                available_tools = []
            visible_catalog, visibility = build_evidence_visibility(
                call_id=f"{state['run_id']}:career_planning:{turn_index + 1}",
                evidence_catalog=evidence_catalog,
            )
            using_plan_path = not available_tools
            publisher = StreamProgressPublisher(
                session_factory=self._session_factory,
                run_id=state["run_id"],
                node_name="career_planning_agent",
                turn=turn_index + 1,
            )
            with bind_stream_delta_sink(publisher.on_delta):
                if available_tools:
                    raw = await self._provider.generate_agent_turn(
                        message=state["request"].message,
                        context=context,
                        replan_mode=mode,
                        available_tools=available_tools,
                        evidence_catalog=visible_catalog,
                        force_final=force_final,
                    )
                else:
                    raw = await self._provider.generate_plan(
                        message=state["request"].message,
                        context=context,
                        replan_mode=mode,
                        evidence_catalog=visible_catalog,
                    )
            stream_summaries.append(publisher.summary())
            usage = extract_provider_usage(raw)
            self._budget.record_llm_call(usage.tokens_in, usage.tokens_out)
            total_usage = (
                usage if total_usage is None else combine_provider_usage(total_usage, usage)
            )
            try:
                if using_plan_path:
                    response = ProviderPlanResponse.model_validate(raw)
                    return NodeOutput(
                        (
                            response.candidate,
                            evidence_catalog,
                            visibility,
                            tool_round,
                            tool_call_count,
                        ),
                        step_telemetry(visibility),
                    )
                turn = AgentTurnResponse.model_validate(raw)
            except ValidationError:
                return await self._repair_format(
                    state=state,
                    raw=raw,
                    evidence_catalog=evidence_catalog,
                    tool_round=tool_round,
                    tool_call_count=tool_call_count,
                    total_usage=total_usage,
                    stream_summaries=stream_summaries,
                )
            if turn.final is not None:
                return NodeOutput(
                    (
                        turn.final,
                        evidence_catalog,
                        visibility,
                        tool_round,
                        tool_call_count,
                    ),
                    step_telemetry(visibility),
                )
            if force_final:
                raise StructuredOutputError("Tool requested after Tool budget was exhausted")
            tool_round += 1
            remaining_calls = state["runtime_config"].max_tool_calls - tool_call_count
            accepted = list(turn.tool_calls[:remaining_calls])
            tool_call_count += len(accepted)
            await self._execute_tools(
                state=state,
                step_id=step_id,
                tool_round=tool_round,
                accepted=accepted,
                evidence_catalog=evidence_catalog,
            )

        if total_usage is None:
            raise StructuredOutputError("Agent produced no turn")
        state["fallback_reason"] = "tool_round_exhausted"
        fallback = fallback_candidate(context, mode)
        _, fallback_visibility = build_evidence_visibility(
            call_id=f"{state['run_id']}:tool_fallback",
            evidence_catalog=[],
        )
        return NodeOutput(
            (
                fallback,
                evidence_catalog,
                fallback_visibility,
                tool_round,
                tool_call_count,
            ),
            step_telemetry(fallback_visibility),
        )

    async def _preload_memory(
        self,
        *,
        state: PlanningState,
        step_id: UUID,
        evidence_catalog: list[EvidenceCatalogItem],
        tool_call_count: int,
    ) -> int:
        context = state["planning_context"]
        execution = await self._tool_registry.execute(
            tool_name="memory_lookup",
            arguments={"query": state["request"].message[:200], "limit": 5},
            context=ToolContext(
                run_id=state["run_id"],
                user_id=state["user_id"],
                goal_type=context.profile.goal_type,
                intent=state["intent"].intent,
                requires_fresh_information=state["intent"].requires_fresh_information,
                remaining_deadline_ms=int(self._budget.remaining_seconds() * 1000),
            ),
            step_id=step_id,
            round_number=1,
        )
        if execution.success:
            self._append_evidence(execution.result.evidence, evidence_catalog)
        return tool_call_count + 1

    async def _execute_tools(
        self,
        *,
        state: PlanningState,
        step_id: UUID,
        tool_round: int,
        accepted: Sequence[ProviderToolCall],
        evidence_catalog: list[EvidenceCatalogItem],
    ) -> None:
        if not accepted:
            return
        context = state["planning_context"]
        tool_context = ToolContext(
            run_id=state["run_id"],
            user_id=state["user_id"],
            goal_type=context.profile.goal_type,
            intent=state["intent"].intent,
            requires_fresh_information=state["intent"].requires_fresh_information,
            remaining_deadline_ms=int(self._budget.remaining_seconds() * 1000),
        )
        executions = await asyncio.gather(
            *(
                self._tool_registry.execute(
                    tool_name=call.name,
                    arguments=call.arguments,
                    context=tool_context,
                    step_id=step_id,
                    round_number=tool_round,
                )
                for call in accepted
            )
        )
        for execution in executions:
            if execution.success:
                self._append_evidence(execution.result.evidence, evidence_catalog)

    async def _repair_format(
        self,
        *,
        state: PlanningState,
        raw: Mapping[str, object],
        evidence_catalog: list[EvidenceCatalogItem],
        tool_round: int,
        tool_call_count: int,
        total_usage: ProviderUsage,
        stream_summaries: list[dict[str, object]],
    ) -> NodeOutput[CandidateResult]:
        context = state["planning_context"]
        mode = state["intent"].replan_mode
        self._budget.claim_format_repair()
        repair_catalog, repair_visibility = build_evidence_visibility(
            call_id=f"{state['run_id']}:format_repair",
            evidence_catalog=evidence_catalog,
        )
        repaired = await self._provider.repair_format(
            raw_output=raw,
            context=context,
            replan_mode=mode,
            evidence_catalog=repair_catalog,
        )
        repair_usage = extract_provider_usage(repaired)
        self._budget.record_llm_call(repair_usage.tokens_in, repair_usage.tokens_out)
        combined_usage = combine_provider_usage(total_usage, repair_usage)
        telemetry = build_provider_telemetry(
            combined_usage,
            state["runtime_config"].prompt_versions["format_repair"],
            repair_visibility,
        )
        if stream_summaries:
            telemetry.trace_data["llm_stream"] = stream_summaries
        try:
            response = ProviderPlanResponse.model_validate(repaired)
        except ValidationError:
            state["fallback_reason"] = "format_repair_failed"
            fallback = fallback_candidate(context, mode)
            _, fallback_visibility = build_evidence_visibility(
                call_id=f"{state['run_id']}:format_fallback",
                evidence_catalog=[],
            )
            return NodeOutput(
                (
                    fallback,
                    evidence_catalog,
                    fallback_visibility,
                    tool_round,
                    tool_call_count,
                ),
                telemetry,
            )
        state["plan_provenance"] = "format_repair"
        return NodeOutput(
            (
                response.candidate,
                evidence_catalog,
                repair_visibility,
                tool_round,
                tool_call_count,
            ),
            telemetry,
        )

    @staticmethod
    def _append_evidence(
        evidence: Sequence[EvidenceItem],
        evidence_catalog: list[EvidenceCatalogItem],
    ) -> None:
        for item in evidence:
            catalog_item = EvidenceCatalogItem.model_validate(item, from_attributes=True)
            if all(
                existing.kind != catalog_item.kind or existing.id != catalog_item.id
                for existing in evidence_catalog
            ):
                evidence_catalog.append(catalog_item)
