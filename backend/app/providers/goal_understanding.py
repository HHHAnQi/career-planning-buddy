"""Optional LLM and deterministic adapters for goal-slot extraction."""

import json
import re
from typing import Protocol

import httpx

from app.core.config import Settings
from app.prompts.goal_understanding import goal_understanding_messages
from app.providers.llm_client import LLMClient, OpenAIChatLLMClient
from app.providers.llm_contracts import LLMMessage, LLMRequest
from app.providers.llm_profiles import model_for_operation, resolve_provider_profile
from app.schemas.enums import ObjectiveType
from app.schemas.goal_briefs import GoalExtraction

OBJECTIVE_PATTERNS: tuple[tuple[ObjectiveType, re.Pattern[str]], ...] = (
    (
        ObjectiveType.CAREER_PLAN,
        re.compile(r"求职|职业|岗位规划|秋招|春招|job search|career", re.I),
    ),
    (ObjectiveType.APPLICATION, re.compile(r"投递|申请|简历投递|application|apply", re.I)),
    (ObjectiveType.INTERVIEW, re.compile(r"面试|mock interview|interview", re.I)),
    (ObjectiveType.PROJECT, re.compile(r"项目|作品|portfolio|系统|应用", re.I)),
    (
        ObjectiveType.SKILL_TRANSITION,
        re.compile(r"技能|学习|转型|转行|skill|transition", re.I),
    ),
)


def classify_objective_type(message: str) -> ObjectiveType | None:
    return next(
        (
            objective_type
            for objective_type, pattern in OBJECTIVE_PATTERNS
            if pattern.search(message)
        ),
        None,
    )


def parse_duration_weeks(message: str) -> int | None:
    """Parse the bounded Chinese duration phrases supported by the product."""
    week_match = re.search(r"([1-8一二两三四五六七八])\s*(?:周|星期)", message)
    week_map = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
    }
    if week_match:
        raw = week_match.group(1)
        return int(raw) if raw.isdigit() else week_map[raw]
    if re.search(r"半个?月", message):
        return 2
    month_match = re.search(r"([12一二两])\s*个?月", message)
    if month_match:
        raw = month_match.group(1)
        months = int(raw) if raw.isdigit() else 1 if raw == "一" else 2
        return months * 4
    return None


class GoalUnderstandingProvider(Protocol):
    model_id: str
    method: str

    async def extract(
        self,
        message: str,
        *,
        planning_days: int | None = None,
        daily_budget_minutes: int | None = None,
    ) -> GoalExtraction: ...


class RuleGoalUnderstandingProvider:
    model_id = "goal-rules-v2"
    method = "rule"

    async def extract(
        self,
        message: str,
        *,
        planning_days: int | None = None,
        daily_budget_minutes: int | None = None,
    ) -> GoalExtraction:
        roles = {
            "AI 后端工程师": r"AI\s*后端|人工智能后端",
            "Agent 应用工程师": r"Agent|智能体",
            "Java 后端工程师": r"Java\s*后端",
            "数据工程师": r"数据工程师|数据开发",
            "全栈工程师": r"全栈",
        }
        target = next(
            (label for label, pattern in roles.items() if re.search(pattern, message, re.I)), None
        )
        technologies = [
            name
            for name in (
                "Python",
                "Java",
                "FastAPI",
                "Spring Boot",
                "React",
                "LangGraph",
                "Docker",
                "PostgreSQL",
            )
            if re.search(re.escape(name), message, re.I)
        ]
        objective_type = classify_objective_type(message)
        requested_weeks = parse_duration_weeks(message)
        available_minutes = (
            planning_days * daily_budget_minutes
            if planning_days is not None and daily_budget_minutes is not None
            else None
        )
        tight = (
            planning_days is not None
            and (
                (requested_weeks is not None and requested_weeks * 7 > planning_days)
                or (objective_type == ObjectiveType.PROJECT and planning_days < 7)
                or (available_minutes is not None and available_minutes < 300)
            )
        )
        return GoalExtraction(
            objective_type=objective_type,
            target_role=target,
            objective=message.strip() if objective_type is not None else None,
            tech_stack=technologies,
            duration_weeks=requested_weeks,
            feasibility="tight" if tight else "feasible" if planning_days is not None else None,
            feasibility_reason=(
                "目标范围与已选时间或每日投入相比偏紧"
                if tight
                else None
            ),
            constrained_strategy=(
                "保留一个可验证核心结果，按优先级压缩非必要功能和文档范围"
                if tight
                else None
            ),
        )


class OpenAICompatibleGoalUnderstandingProvider:
    method = "model"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
        provider_name: str = "auto",
        reasoning: str = "off",
        client: LLMClient | None = None,
    ) -> None:
        self.model_id = model
        self._reasoning = "off" if reasoning == "off" else "auto"
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

    async def extract(
        self,
        message: str,
        *,
        planning_days: int | None = None,
        daily_budget_minutes: int | None = None,
    ) -> GoalExtraction:
        response = await self._client.complete(
            LLMRequest(
                operation="goal_understanding",
                model=self.model_id,
                messages=[
                    LLMMessage.model_validate(item)
                    for item in goal_understanding_messages(
                        message,
                        planning_days=planning_days,
                        daily_budget_minutes=daily_budget_minutes,
                    )
                ],
                structured_output="json_object",
                reasoning=self._reasoning,
                temperature=0,
                max_output_tokens=700,
            )
        )
        if response.content is None:
            raise ValueError("goal Provider returned empty content")
        return GoalExtraction.model_validate(json.loads(response.content))

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def build_goal_understanding_provider(
    settings: Settings,
    *,
    client: LLMClient | None = None,
) -> GoalUnderstandingProvider:
    if settings.llm_provider == "mock":
        return RuleGoalUnderstandingProvider()
    assert settings.llm_api_key is not None
    assert settings.llm_base_url is not None
    assert settings.llm_model is not None
    return OpenAICompatibleGoalUnderstandingProvider(
        api_key=settings.llm_api_key.get_secret_value(),
        base_url=str(settings.llm_base_url),
        model=model_for_operation(settings, "goal_understanding"),
        timeout_seconds=settings.llm_timeout_seconds,
        provider_name=settings.llm_provider_name,
        reasoning=settings.llm_goal_understanding_reasoning,
        client=client,
    )
