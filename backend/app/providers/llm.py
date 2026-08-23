"""Provider protocol and deterministic Stage 4 planning adapter."""

import asyncio
import json
from collections.abc import Mapping
from datetime import timedelta
from typing import Any, Protocol

import httpx
from pydantic import ValidationError

from app.agent.errors import ProviderConfigurationError
from app.core.config import Settings
from app.prompts.career_planning import (
    business_repair_messages,
    direct_baseline_messages,
    format_repair_messages,
    generation_messages,
)
from app.providers.llm_client import LLMClient, OpenAIChatLLMClient
from app.providers.llm_contracts import (
    LLMMessage,
    LLMRequest,
    LLMResponse,
    LLMToolDefinition,
)
from app.providers.llm_profiles import model_for_operation, resolve_provider_profile
from app.providers.streaming import current_stream_delta_sink
from app.schemas.agent_runs import (
    AgentTurnResponse,
    EvidenceCatalogItem,
    PlanCandidate,
    PlanningContext,
    ProviderPlanResponse,
    ProviderToolCall,
    ProviderUsage,
    TaskCandidate,
    WeeklyFocusCandidate,
)
from app.schemas.enums import GoalType, ReplanMode, TaskType
from app.tools.contracts import ModelToolSpec


def _action_day_count(context: PlanningContext) -> int:
    window = context.planning_window
    remaining = (window.horizon_end - window.planning_date).days + 1
    return max(1, min(7, remaining))


class PlanningProvider(Protocol):
    async def generate_agent_turn(
        self,
        *,
        message: str,
        context: PlanningContext,
        replan_mode: ReplanMode,
        available_tools: list[ModelToolSpec],
        evidence_catalog: list[EvidenceCatalogItem],
        force_final: bool,
    ) -> Mapping[str, object]: ...

    async def generate_plan(
        self,
        *,
        message: str,
        context: PlanningContext,
        replan_mode: ReplanMode,
        evidence_catalog: list[EvidenceCatalogItem],
    ) -> Mapping[str, object]: ...

    async def repair_format(
        self,
        *,
        raw_output: Mapping[str, object],
        context: PlanningContext,
        replan_mode: ReplanMode,
        evidence_catalog: list[EvidenceCatalogItem],
    ) -> Mapping[str, object]: ...

    async def repair_business_rules(
        self,
        *,
        candidate: PlanCandidate,
        context: PlanningContext,
        repair_instructions: list[str],
        message: str,
        replan_mode: ReplanMode,
        evidence_catalog: list[EvidenceCatalogItem],
    ) -> Mapping[str, object]: ...


class OpenAICompatiblePlanningProvider:
    """Planning use-case adapter over the provider-neutral LLM client."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 30,
        max_output_tokens: int = 1500,
        transport: httpx.AsyncBaseTransport | None = None,
        provider_name: str = "auto",
        reasoning: str = "off",
        client: LLMClient | None = None,
        streaming_enabled: bool = False,
    ) -> None:
        if not api_key or not base_url or not model:
            raise ProviderConfigurationError(
                "openai_compatible requires API key, base URL, and model"
            )
        self._model = model
        self._max_output_tokens = max_output_tokens
        self._reasoning = "off" if reasoning == "off" else "auto"
        self._streaming_enabled = streaming_enabled
        self._owns_client = client is None
        self._client = client or OpenAIChatLLMClient(
            api_key=api_key,
            base_url=base_url,
            profile=resolve_provider_profile(
                configured_name=provider_name,
                base_url=base_url,
            ),
            timeout_seconds=timeout_seconds,
            transport=transport,
        )

    async def _complete_request(self, request: LLMRequest) -> LLMResponse:
        """Complete via SSE streaming when enabled, forwarding text deltas.

        Streaming preserves the non-streaming contract: the assembled
        ``LLMResponse`` is identical, so graph validation, repair, and the
        eval harness are unaffected. A sink bound by the graph (ContextVar)
        receives each delta; without one, deltas are simply discarded.
        """
        streamed: Any = getattr(self._client, "complete_streamed", None)
        if self._streaming_enabled and callable(streamed):
            response: LLMResponse = await streamed(
                request, on_delta=current_stream_delta_sink()
            )
            return response
        return await self._client.complete(request)

    async def generate_plan(
        self,
        *,
        message: str,
        context: PlanningContext,
        replan_mode: ReplanMode,
        evidence_catalog: list[EvidenceCatalogItem],
    ) -> Mapping[str, object]:
        return await self._generate(
            generation_messages(
                message=message,
                context=context,
                replan_mode=replan_mode,
                evidence_catalog=evidence_catalog,
            ),
            operation="planning",
        )

    async def generate_direct_plan(
        self,
        *,
        message: str,
        context: PlanningContext,
        replan_mode: ReplanMode,
    ) -> Mapping[str, object]:
        return await self._generate(
            direct_baseline_messages(
                message=message,
                context=context,
                replan_mode=replan_mode,
            ),
            operation="planning",
        )

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
        messages = generation_messages(
            message=message,
            context=context,
            replan_mode=replan_mode,
            evidence_catalog=evidence_catalog,
        )
        tools = available_tools if available_tools and not force_final else []
        response = await self._complete_request(
            self._request(
                operation="planning",
                messages=messages,
                tools=tools,
            )
        )
        usage = self._provider_usage(response)
        if response.tool_calls and response.content:
            return {
                "_raw_text": "mixed tool calls and final",
                "usage": usage.model_dump(mode="json"),
            }
        if response.tool_calls:
            return AgentTurnResponse(
                tool_calls=[
                    ProviderToolCall(
                        call_id=call.call_id,
                        name=call.name,
                        arguments=call.arguments,
                    )
                    for call in response.tool_calls
                ],
                usage=usage,
            ).model_dump(mode="json")
        if response.content:
            try:
                candidate_object: object = json.loads(response.content)
                candidate = PlanCandidate.model_validate(candidate_object)
            except (json.JSONDecodeError, ValidationError):
                return {
                    "_raw_text": response.content[:12000],
                    "usage": usage.model_dump(mode="json"),
                }
            return AgentTurnResponse(final=candidate, usage=usage).model_dump(mode="json")
        return {"_raw_text": "", "usage": usage.model_dump(mode="json")}

    async def repair_format(
        self,
        *,
        raw_output: Mapping[str, object],
        context: PlanningContext,
        replan_mode: ReplanMode,
        evidence_catalog: list[EvidenceCatalogItem],
    ) -> Mapping[str, object]:
        return await self._generate(
            format_repair_messages(
                raw_output=raw_output,
                context=context,
                replan_mode=replan_mode,
                evidence_catalog=evidence_catalog,
            ),
            operation="format_repair",
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
        return await self._generate(
            business_repair_messages(
                candidate=candidate,
                context=context,
                repair_instructions=repair_instructions,
                message=message,
                replan_mode=replan_mode,
                evidence_catalog=evidence_catalog,
            ),
            operation="business_repair",
        )

    async def _generate(
        self,
        messages: list[dict[str, str]],
        *,
        operation: str,
    ) -> Mapping[str, object]:
        response = await self._complete_request(
            self._request(operation=operation, messages=messages)
        )
        usage = self._provider_usage(response)
        if response.content is None:
            return {
                "_raw_text": "",
                "usage": usage.model_dump(mode="json"),
            }
        try:
            candidate_object: object = json.loads(response.content)
        except json.JSONDecodeError:
            return {
                "_raw_text": response.content[:12000],
                "usage": usage.model_dump(mode="json"),
            }
        if not isinstance(candidate_object, Mapping):
            return {
                "_raw_text": response.content[:12000],
                "usage": usage.model_dump(mode="json"),
            }
        candidate = {str(key): value for key, value in candidate_object.items()}
        return {
            "candidate": candidate,
            "usage": usage.model_dump(mode="json"),
        }

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _request(
        self,
        *,
        operation: str,
        messages: list[dict[str, str]],
        tools: list[ModelToolSpec] | None = None,
    ) -> LLMRequest:
        return LLMRequest(
            operation=operation,
            model=self._model,
            messages=[LLMMessage.model_validate(message) for message in messages],
            tools=[
                LLMToolDefinition(
                    name=tool.name,
                    description=tool.description,
                    input_json_schema=tool.input_json_schema,
                )
                for tool in tools or []
            ],
            tool_choice="auto" if tools else "none",
            structured_output="json_object",
            reasoning=self._reasoning,
            temperature=0.1,
            max_output_tokens=self._max_output_tokens,
        )

    @staticmethod
    def _provider_usage(response: LLMResponse) -> ProviderUsage:
        return ProviderUsage(
            model_id=response.model_id,
            provider=response.provider_id,
            request_id=response.request_id,
            raw_output_hash=response.raw_output_hash,
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
            latency_ms=response.latency_ms,
        )


class DirectLLMPlanningProvider:
    """LLM-only Eval arm with no tool, memory, or evidence visibility."""

    def __init__(self, delegate: OpenAICompatiblePlanningProvider) -> None:
        self._delegate = delegate

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
        del available_tools, evidence_catalog, force_final
        raw = await self.generate_plan(
            message=message,
            context=context,
            replan_mode=replan_mode,
            evidence_catalog=[],
        )
        candidate = raw.get("candidate")
        usage = raw.get("usage")
        if candidate is None or usage is None:
            return raw
        return {"final": candidate, "tool_calls": [], "usage": usage}

    async def generate_plan(
        self,
        *,
        message: str,
        context: PlanningContext,
        replan_mode: ReplanMode,
        evidence_catalog: list[EvidenceCatalogItem],
    ) -> Mapping[str, object]:
        del evidence_catalog
        return await self._delegate.generate_direct_plan(
            message=message,
            context=context,
            replan_mode=replan_mode,
        )

    async def repair_format(
        self,
        *,
        raw_output: Mapping[str, object],
        context: PlanningContext,
        replan_mode: ReplanMode,
        evidence_catalog: list[EvidenceCatalogItem],
    ) -> Mapping[str, object]:
        del evidence_catalog
        return await self._delegate.repair_format(
            raw_output=raw_output,
            context=_direct_context(context),
            replan_mode=replan_mode,
            evidence_catalog=[],
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
        del evidence_catalog
        return await self._delegate.repair_business_rules(
            candidate=candidate,
            context=_direct_context(context),
            repair_instructions=repair_instructions,
            message=message,
            replan_mode=replan_mode,
            evidence_catalog=[],
        )


def _direct_context(context: PlanningContext) -> PlanningContext:
    return context.model_copy(
        update={
            "recent_tasks": [],
            "recent_reviews": [],
            "completed_facts": [],
            "blockers": [],
            "pinned_memories": [],
            "task_history_summary": None,
            "review_history_summary": None,
            "token_estimate": 0,
        }
    )


class MockPlanningProvider:
    """Deterministic provider whose scenarios are selected by test-safe message markers."""

    model_id = "mock-career-planner-v1"

    def __init__(self) -> None:
        self.plan_calls = 0
        self.format_repair_calls = 0
        self.business_repair_calls = 0

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
        self.plan_calls += 1
        if "[mock:timeout]" in message:
            await asyncio.sleep(60)
        candidate = self._candidate(context, replan_mode)
        if "[mock:invalid-schema]" in message or "[mock:invalid-schema-twice]" in message:
            invalid_payload = candidate.model_dump(mode="json")
            invalid_payload["tasks"] = []
            return {
                "candidate": invalid_payload,
                "usage": self._usage().model_dump(mode="json"),
                "_mock_scenario": (
                    "invalid-schema-twice"
                    if "[mock:invalid-schema-twice]" in message
                    else "invalid-schema"
                ),
            }
        if "[mock:rule-repair]" in message or "[mock:rule-fallback]" in message:
            invalid_candidate = self._over_budget_candidate(candidate, context)
            return AgentTurnResponse(
                final=invalid_candidate,
                usage=self._usage(),
            ).model_dump(mode="json")
        requested: list[str] = []
        if "[mock:tool-all]" in message:
            requested = ["memory_lookup", "rag_retrieve"]
        elif "[mock:tool-memory]" in message:
            requested = ["memory_lookup"]
        elif "[mock:tool-rag]" in message:
            requested = ["rag_retrieve"]
        elif "[mock:tool-search]" in message:
            requested = ["web_search"]
        elif "[mock:tool-unknown]" in message:
            requested = ["unregistered_tool"]
        if requested and not evidence_catalog and not force_final:
            calls: list[ProviderToolCall] = []
            for index, name in enumerate(requested):
                query = (
                    "[mock:search-timeout]"
                    if "[mock:search-timeout]" in message
                    else "[mock:search-error]"
                    if "[mock:search-error]" in message
                    else "[mock:embedding-error]"
                    if "[mock:embedding-error]" in message
                    else "Agent 工程求职证据"
                )
                arguments: dict[str, object] = {"query": query, "limit": 3}
                if name == "rag_retrieve":
                    arguments["goal_type"] = context.profile.goal_type.value
                if "[mock:tool-invalid]" in message:
                    arguments["unexpected"] = True
                calls.append(
                    ProviderToolCall(
                        call_id=f"mock-call-{index + 1}",
                        name=name,
                        arguments=arguments,
                    )
                )
            return AgentTurnResponse(
                tool_calls=calls,
                usage=self._usage(tokens_in=120, tokens_out=40),
            ).model_dump(mode="json")
        allowed_evidence = evidence_catalog[:10]
        if allowed_evidence:
            candidate_payload = candidate.model_dump(mode="python")
            candidate_payload["evidence_refs"] = [
                {"kind": item.kind, "id": item.id} for item in allowed_evidence
            ]
            candidate = PlanCandidate.model_validate(candidate_payload)
        if "[mock:forged-evidence]" in message:
            from uuid import UUID

            candidate_payload = candidate.model_dump(mode="python")
            candidate_payload["evidence_refs"] = [
                {
                    "kind": "memory",
                    "id": UUID("00000000-0000-0000-0000-000000000001"),
                }
            ]
            candidate = PlanCandidate.model_validate(candidate_payload)
        return AgentTurnResponse(
            final=PlanCandidate.model_validate(candidate),
            usage=self._usage(),
        ).model_dump(mode="json")

    async def generate_plan(
        self,
        *,
        message: str,
        context: PlanningContext,
        replan_mode: ReplanMode,
        evidence_catalog: list[EvidenceCatalogItem],
    ) -> Mapping[str, object]:
        self.plan_calls += 1
        if "[mock:timeout]" in message:
            await asyncio.sleep(60)
        candidate = self._candidate(context, replan_mode)
        if "[mock:invalid-schema]" in message or "[mock:invalid-schema-twice]" in message:
            invalid_payload = candidate.model_dump(mode="json")
            invalid_payload["tasks"] = []
            return {
                "candidate": invalid_payload,
                "usage": self._usage().model_dump(mode="json"),
                "_mock_scenario": (
                    "invalid-schema-twice"
                    if "[mock:invalid-schema-twice]" in message
                    else "invalid-schema"
                ),
            }
        if "[mock:rule-repair]" in message or "[mock:rule-fallback]" in message:
            invalid_candidate = self._over_budget_candidate(candidate, context)
            return ProviderPlanResponse(
                candidate=invalid_candidate, usage=self._usage()
            ).model_dump(mode="json")
        if evidence_catalog and "[mock:tool-" in message:
            payload = candidate.model_dump(mode="python")
            payload["evidence_refs"] = [
                {"kind": item.kind, "id": item.id} for item in evidence_catalog[:10]
            ]
            candidate = PlanCandidate.model_validate(payload)
        if "[mock:forged-evidence]" in message:
            from uuid import UUID

            payload = candidate.model_dump(mode="python")
            payload["evidence_refs"] = [
                {
                    "kind": "memory",
                    "id": UUID("00000000-0000-0000-0000-000000000001"),
                }
            ]
            candidate = PlanCandidate.model_validate(payload)
        return ProviderPlanResponse(candidate=candidate, usage=self._usage()).model_dump(
            mode="json"
        )

    async def repair_format(
        self,
        *,
        raw_output: Mapping[str, object],
        context: PlanningContext,
        replan_mode: ReplanMode,
        evidence_catalog: list[EvidenceCatalogItem],
    ) -> Mapping[str, object]:
        self.format_repair_calls += 1
        if raw_output.get("_mock_scenario") == "invalid-schema-twice":
            return raw_output
        return ProviderPlanResponse(
            candidate=self._candidate(context, replan_mode),
            usage=self._usage(tokens_in=180, tokens_out=320),
        ).model_dump(mode="json")

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
        self.business_repair_calls += 1
        del candidate, repair_instructions, evidence_catalog
        repaired = self._candidate(context, replan_mode)
        if "[mock:rule-fallback]" in message:
            repaired = self._over_budget_candidate(repaired, context)
        return ProviderPlanResponse(
            candidate=repaired,
            usage=self._usage(tokens_in=250, tokens_out=400),
        ).model_dump(mode="json")

    def _candidate(
        self, context: PlanningContext, replan_mode: ReplanMode
    ) -> PlanCandidate:
        window = context.planning_window
        goal_label = {
            GoalType.AI_BACKEND: "AI 后端",
            GoalType.AGENT_APP: "Agent 应用",
            GoalType.BACKEND_JAVA: "Java 后端",
            GoalType.DATA_ENGINEER: "数据工程",
            GoalType.FULLSTACK: "全栈工程师",
            GoalType.OTHER: "目标",
        }[context.profile.goal_type]
        weekly_templates = [
            (f"明确{goal_label}岗位与能力差距", "形成岗位要求与能力差距清单"),
            ("完成可展示的项目核心增量", "产出可运行、可验证的项目成果"),
            ("沉淀简历与项目表达材料", "形成可投递的简历项目描述"),
            ("开展模拟面试并验证准备效果", "完成复盘记录并修正薄弱点"),
            ("扩大岗位样本并校准投递方向", "形成目标岗位优先级列表"),
            ("强化高频面试专题与表达", "完成一轮专题问答演练"),
            ("集中投递并跟踪反馈", "形成投递与反馈跟踪表"),
            ("复盘结果并确定下一阶段策略", "形成下一阶段行动决策"),
        ]
        if replan_mode == ReplanMode.CONTINUE and context.source_plan is not None:
            source_weekly = [
                WeeklyFocusCandidate.model_validate(item.model_dump())
                for item in context.source_plan.weekly_focus[
                    : window.horizon_weeks
                ]
            ]
            weekly = (
                source_weekly
                if len(source_weekly) == window.horizon_weeks
                and len({item.focus for item in source_weekly}) == len(source_weekly)
                else [
                    WeeklyFocusCandidate(
                        week_index=index,
                        focus=weekly_templates[index - 1][0],
                        success_signal=weekly_templates[index - 1][1],
                    )
                    for index in range(1, window.horizon_weeks + 1)
                ]
            )
        else:
            weekly = [
                WeeklyFocusCandidate(
                    week_index=index,
                    focus=weekly_templates[index - 1][0],
                    success_signal=weekly_templates[index - 1][1],
                )
                for index in range(1, window.horizon_weeks + 1)
            ]
        first_week_focus = weekly[0].focus
        daily_templates = [
            (
                f"收集{goal_label}岗位样本",
                TaskType.LEARNING,
                f"1. 收集3份{goal_label}JD；2. 保存岗位链接与公司信息；3. 标出重复要求",
                f"岗位样本表，包含3份{goal_label}JD、来源及重复要求",
            ),
            (
                "提取岗位能力要求",
                TaskType.LEARNING,
                "1. 合并3份JD要求；2. 按前端、后端、数据和工程化分类；3. 统计出现频次",
                "岗位要求清单，包含至少10项能力、分类及出现频次",
            ),
            (
                "盘点现有能力证据",
                TaskType.LEARNING,
                "1. 逐项标记已掌握、待补或待证明；2. 关联项目与学习经历；3. 标出证据缺口",
                "能力证据表，包含至少10项要求、当前水平及已有证据",
            ),
            (
                "建立能力差距矩阵",
                TaskType.LEARNING,
                "1. 对照岗位要求与现有证据；2. 标记能力、经验和表达差距；3. 写明判断依据",
                "能力差距矩阵，包含至少5项差距、类型及判断依据",
            ),
            (
                "排定差距补齐优先级",
                TaskType.LEARNING,
                "1. 按岗位频次和当前差距评分；2. 估算补齐成本；3. 选出前三项优先差距",
                "差距优先级表，包含至少5项评分、成本及前三项结论",
            ),
            (
                "复核首要能力差距",
                TaskType.LEARNING,
                "1. 新增2份同类JD；2. 核对前三项差距是否高频；3. 修正不一致的排序",
                "差距复核记录，包含2份新增JD、核对结果及排序调整",
            ),
            (
                "汇总岗位与能力结论",
                TaskType.OTHER,
                "1. 汇总岗位要求与差距矩阵；2. 写出前三项差距；3. 确定下周项目增量方向",
                "第一周结论，包含岗位要求清单、前三项差距和下周方向",
            ),
        ]
        cycle_days = _action_day_count(context)
        minutes = max(5, min(45, context.time_budget_minutes))
        tasks = [
            TaskCandidate(
                title=title,
                task_type=task_type,
                scheduled_date=window.planning_date + timedelta(days=day_offset),
                starter_action=starter_action,
                deliverable=(
                    f"{(window.planning_date + timedelta(days=day_offset)).isoformat()} "
                    f"{deliverable}"
                ),
                estimated_minutes=minutes,
                rationale=f"服务第1周目标“{first_week_focus}”并形成可验证证据",
            )
            for day_offset, (title, task_type, starter_action, deliverable) in enumerate(
                daily_templates[:cycle_days]
            )
        ]
        adjustment_reason = None
        if replan_mode == ReplanMode.ADJUST:
            review = context.source_review
            adjustment_reason = (
                review.adjustment_request
                if review and review.adjustment_request
                else review.replan_reason
                if review and review.replan_reason
                else review.blockers
                if review and review.blockers
                else "根据当前阻碍采用更小的行动步长"
            )
        return PlanCandidate(
            plan_date=window.planning_date,
            horizon_start=window.horizon_start,
            horizon_end=window.horizon_end,
            overall_direction=(
                context.source_plan.overall_direction
                if context.source_plan is not None
                else "在规划窗口内形成可展示项目证据并推进面试准备"
            ),
            weekly_focus=weekly,
            summary="七天内按定位、项目、表达和复盘逐步推进",
            rationale="每个日期只安排一个关键结果，并分别限制在每日时间预算内。",
            adjustment_reason=adjustment_reason,
            assumptions=["计划基于当前画像与每日时间预算"],
            tasks=tasks,
            evidence_refs=[],
        )

    @staticmethod
    def _over_budget_candidate(
        candidate: PlanCandidate,
        context: PlanningContext,
    ) -> PlanCandidate:
        if len(candidate.tasks) < 2:
            return candidate.model_copy(
                update={
                    "tasks": [
                        candidate.tasks[0].model_copy(
                            update={"estimated_minutes": context.time_budget_minutes + 1}
                        )
                    ]
                }
            )
        first_date = candidate.tasks[0].scheduled_date
        invalid = list(candidate.tasks)
        invalid[0] = invalid[0].model_copy(
            update={"estimated_minutes": context.time_budget_minutes}
        )
        invalid[1] = invalid[1].model_copy(
            update={
                "scheduled_date": first_date,
                "estimated_minutes": context.time_budget_minutes,
            }
        )
        return candidate.model_copy(update={"tasks": invalid})

    def _usage(self, *, tokens_in: int = 300, tokens_out: int = 450) -> ProviderUsage:
        return ProviderUsage(
            model_id=self.model_id,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=5,
        )


class PairSmokePlanningProvider(MockPlanningProvider):
    """Two deterministic, semantically-comparable fixture profiles used
    ONLY by Stage A pairwise smoke verification (PR-9c.2 Commit 3.4 / E′).

    Invariants (per reviewer-approved design):

    * same ``PlanningContext`` + same profile  → byte-identical output
    * same ``PlanningContext`` + diff profile  → byte-different output
    * no experiment-identity leakage (no UUID / baseline/candidate
      strings / "[candidate-variant]" markers / thread-local / global)
    * no ``PlanningContext`` mutation
    * safety/System/Tool-marker semantics unchanged vs MockPlanningProvider
    * no import of ``evals.*`` (provider layer stays below evals runtime)

    Two profiles:
      * ``compact_v1``  — seven terse, high-leverage daily tasks.
      * ``structured_v1``— seven explicitly-staged daily tasks with
                           richer decomposition and rationale.

    Differences are REAL (actionability, clarity, decomposition), not
    numeric noise. Both summaries differ in wording so ``PLAN_PROJECTION``
    (which includes ``summary``) produces byte-different ``output_hash``
    via the loader's ``canonical_sha256({request, plan})`` formula.

    The profile choice is sourced from ``Settings.eval_pair_smoke_planning_profile``
    — the only construction-side input.
    """

    model_id = "pair-smoke-career-planner-v1"

    _ALLOWED_PROFILES = frozenset({"compact_v1", "structured_v1"})

    def __init__(self, profile: str) -> None:
        super().__init__()
        if profile not in self._ALLOWED_PROFILES:
            raise ValueError(
                f"PairSmokePlanningProvider: unknown profile {profile!r}; "
                f"expected one of {sorted(self._ALLOWED_PROFILES)}"
            )
        self._profile = profile

    def _candidate(
        self, context: PlanningContext, replan_mode: ReplanMode
    ) -> PlanCandidate:
        """Profile-specific candidate builder.

        Reuses the parent's weekly-focus + horizon scaffolding so the
        planner-side invariants (contiguous ``week_index``, identical
        ``plan_date`` / ``horizon_start`` / ``horizon_end`` across
        profiles) are preserved; only the profile-visible parts
        (``summary``, ``rationale``, ``tasks``) differ.
        """

        # Shared scaffolding (identical across profiles for comparability).
        window = context.planning_window
        if replan_mode == ReplanMode.CONTINUE and context.source_plan is not None:
            weekly = [
                WeeklyFocusCandidate.model_validate(item.model_dump())
                for item in context.source_plan.weekly_focus[
                    : window.horizon_weeks
                ]
            ]
            while len(weekly) < window.horizon_weeks:
                index = len(weekly) + 1
                weekly.append(
                    WeeklyFocusCandidate(
                        week_index=index,
                        focus=f"第 {index} 周继续推进可验证的求职准备成果",
                        success_signal=f"第 {index} 周产出可展示证据",
                    )
                )
        else:
            weekly = [
                WeeklyFocusCandidate(
                    week_index=index,
                    focus=f"第 {index} 周完成一个可验证的求职准备增量",
                    success_signal=f"第 {index} 周产出可展示证据",
                )
                for index in range(1, window.horizon_weeks + 1)
            ]

        # Replan adjustment reason (identical across profiles — keeps
        # safety/replan semantics neutral).
        adjustment_reason = None
        if replan_mode == ReplanMode.ADJUST:
            review = context.source_review
            adjustment_reason = (
                review.adjustment_request
                if review and review.adjustment_request
                else review.replan_reason
                if review and review.replan_reason
                else review.blockers
                if review and review.blockers
                else "根据当前阻碍采用更小的行动步长"
            )

        overall_direction = (
            context.source_plan.overall_direction
            if context.source_plan is not None
            else "在规划窗口内形成可展示项目证据并推进面试准备"
        )

        if self._profile == "compact_v1":
            tasks = [
                TaskCandidate(
                    title=f"第 {day + 1} 天闭环一个求职准备增量",
                    task_type=TaskType.PROJECT,
                    scheduled_date=window.planning_date + timedelta(days=day),
                    starter_action="选择一个能在今天闭环的小改动并执行",
                    deliverable=f"第 {day + 1} 天可验证的求职准备增量",
                    estimated_minutes=max(
                        5, min(30, context.time_budget_minutes)
                    ),
                    rationale="把准备动作压缩为最小可展示单位",
                )
                for day in range(_action_day_count(context))
            ]
            summary = (
                f"[compact_v1] 七天精简版本:每天闭环一个高杠杆增量,"
                f"在 {window.horizon_weeks} 周窗口内形成一条清晰的证据线"
            )
            rationale = (
                "compact_v1 用单一高杠杆任务压缩决策开销,"
                "牺牲分解粒度换取执行确定性"
            )
        else:  # structured_v1
            structured_templates = [
                ("梳理目标岗位能力差距", TaskType.LEARNING, "一份包含三个能力差距的清单"),
                ("确定七天优先级", TaskType.LEARNING, "一份七天行动优先级表"),
                ("完成最小项目增量", TaskType.PROJECT, "一个可运行且有测试结果的项目增量"),
                ("验证项目证据", TaskType.PROJECT, "一份包含测试结果的验证记录"),
                ("整理简历表达", TaskType.RESUME, "三条包含动作和结果的简历要点"),
                ("开展模拟面试", TaskType.INTERVIEW, "一份包含改进点的模拟面试记录"),
                ("复盘并安排下一步", TaskType.LEARNING, "一份七天复盘和下一步清单"),
            ]
            tasks = [
                TaskCandidate(
                    title=title,
                    task_type=task_type,
                    scheduled_date=window.planning_date + timedelta(days=day),
                    starter_action=f"按第 {day + 1} 天模板执行并记录关键过程",
                    deliverable=f"第 {day + 1} 天：{deliverable}",
                    estimated_minutes=max(5, min(30, context.time_budget_minutes)),
                    rationale="按岗位差距、实践证据、表达和复盘的显式链条推进",
                )
                for day, (title, task_type, deliverable) in enumerate(
                    structured_templates[:_action_day_count(context)]
                )
            ]
            summary = (
                f"[structured_v1] 七天结构化版本:本周按差距→实践→表达→复盘"
                f"的显式链条推进,在 {window.horizon_weeks} 周窗口内形成"
                "可追溯的多步证据链"
            )
            rationale = (
                "structured_v1 用三阶段任务链显式承担能力差距→证据"
                "→沉淀,换取清晰度但增加协调开销"
            )

        return PlanCandidate(
            plan_date=window.planning_date,
            horizon_start=window.horizon_start,
            horizon_end=window.horizon_end,
            overall_direction=overall_direction,
            weekly_focus=weekly,
            summary=summary,
            rationale=rationale,
            adjustment_reason=adjustment_reason,
            assumptions=[],
            tasks=tasks,
            evidence_refs=[],
        )


_AGENT_VARIANT_PROFILE_MAP: dict[str, str] = {
    "compact_execution_v1": "compact_v1",
    "structured_reasoning_v1": "structured_v1",
}


def build_planning_provider(
    settings: Settings,
    agent_variant: str | None = None,
    *,
    client: LLMClient | None = None,
) -> PlanningProvider:
    """Build exactly the configured Provider; never silently substitute Mock.

    Stage B-1a-lite (Commit 3.5): when ``agent_variant`` is set and
    ``llm_provider == "mock"``, select a variant-specific deterministic
    provider by mapping the variant name to a PairSmoke profile. This
    lets experiments carry their own variant identity rather than
    relying on the global ``Settings.eval_pair_smoke_planning_profile``.

    ``agent_variant=None`` (or an unrecognized variant under mock) falls
    through to the legacy ``MockPlanningProvider`` path.
    """
    if settings.llm_provider == "mock":
        if agent_variant is not None:
            profile = _AGENT_VARIANT_PROFILE_MAP.get(agent_variant)
            if profile is not None:
                return PairSmokePlanningProvider(profile)
        return MockPlanningProvider()
    if settings.llm_api_key is None or settings.llm_base_url is None or settings.llm_model is None:
        raise ProviderConfigurationError(
            "openai_compatible requires LLM_API_KEY, LLM_BASE_URL, and LLM_MODEL"
        )
    provider = OpenAICompatiblePlanningProvider(
        api_key=settings.llm_api_key.get_secret_value(),
        base_url=str(settings.llm_base_url),
        model=model_for_operation(settings, "planning"),
        timeout_seconds=settings.llm_timeout_seconds,
        max_output_tokens=settings.agent_max_output_tokens_per_call,
        provider_name=settings.llm_provider_name,
        reasoning=settings.llm_planning_reasoning,
        client=client,
        streaming_enabled=settings.llm_streaming_enabled,
    )
    if agent_variant == "direct_llm_v1":
        return DirectLLMPlanningProvider(provider)
    return provider
