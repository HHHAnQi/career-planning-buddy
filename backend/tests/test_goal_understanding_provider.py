"""Goal-understanding Provider compatibility and Chinese parsing tests."""

import json

import httpx
import pytest

from app.providers.goal_understanding import (
    OpenAICompatibleGoalUnderstandingProvider,
    RuleGoalUnderstandingProvider,
    parse_duration_weeks,
)
from app.schemas.enums import ObjectiveType


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("准备两周", 2),
        ("准备半个月", 2),
        ("准备一个月", 4),
        ("准备两个月", 8),
    ],
)
def test_parse_chinese_duration_phrases(message: str, expected: int) -> None:
    assert parse_duration_weeks(message) == expected


@pytest.mark.asyncio
async def test_rule_provider_treats_fullstack_job_search_as_career_plan() -> None:
    result = await RuleGoalUnderstandingProvider().extract(
        "四周内完成全栈工程师求职准备，并包含项目和模拟面试"
    )

    assert result.objective_type == ObjectiveType.CAREER_PLAN
    assert result.target_role == "全栈工程师"
    assert result.duration_weeks == 4


@pytest.mark.asyncio
async def test_rule_provider_warns_when_goal_exceeds_confirmed_window() -> None:
    result = await RuleGoalUnderstandingProvider().extract(
        "两周内完成一个可演示的 Agent 项目",
        planning_days=5,
        daily_budget_minutes=30,
    )

    assert result.feasibility == "tight"
    assert result.feasibility_reason
    assert result.constrained_strategy


@pytest.mark.asyncio
async def test_official_deepseek_disables_thinking_for_goal_json(
) -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "objective_type": "career_plan",
                                    "target_role": "全栈工程师",
                                    "objective": "完成求职准备",
                                    "capability_focus": [],
                                    "tech_stack": [],
                                    "duration_weeks": 4,
                                    "deliverables": [],
                                    "success_criteria": [],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    provider = OpenAICompatibleGoalUnderstandingProvider(
        api_key="test-only",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )

    result = await provider.extract("四周全栈求职准备")

    assert captured["thinking"] == {"type": "disabled"}
    assert result.objective_type == ObjectiveType.CAREER_PLAN


@pytest.mark.asyncio
async def test_other_openai_compatible_goal_provider_omits_thinking(
) -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"objective_type": "career_plan"}
                            )
                        }
                    }
                ]
            },
        )

    provider = OpenAICompatibleGoalUnderstandingProvider(
        api_key="test-only",
        base_url="https://llm.example.com/v1",
        model="compatible-model",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )

    await provider.extract("求职准备")

    assert "thinking" not in captured


@pytest.mark.asyncio
async def test_glm_disables_thinking_for_goal_json(
) -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "glm-4.7",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "objective_type": "project",
                                    "capability_focus": ["RAG"],
                                    "success_criteria": ["可运行"],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ],
            },
        )

    provider = OpenAICompatibleGoalUnderstandingProvider(
        api_key="test-only",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        model="glm-4.7",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )

    result = await provider.extract("设计一个面向岗位的 RAG 项目")

    assert captured["thinking"] == {"type": "disabled"}
    assert result.capability_focus == ["RAG"]
