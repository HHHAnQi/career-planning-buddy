"""Goal Brief clarification and human-confirmation contract tests."""

from http import HTTPStatus

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.api.dependencies import get_goal_understanding_provider
from app.schemas.goal_briefs import GoalExtraction
from tests.test_profile_api import bearer, guest_login, profile_body


@pytest.mark.asyncio
async def test_goal_brief_requires_confirmation_before_agent_run(
    api_client: AsyncClient,
) -> None:
    token, _, _ = await guest_login(api_client)
    await api_client.put(
        "/api/v1/profile",
        json=profile_body(),
        headers={**bearer(token), "Idempotency-Key": "goal-brief-profile"},
    )
    headers = {**bearer(token), "Idempotency-Key": "goal-brief-create"}
    created = await api_client.post(
        "/api/v1/goal-briefs",
        json={
            "message": "设计一个面向岗位的项目",
            "hint_intent": "create_plan",
        },
        headers=headers,
    )
    repeated = await api_client.post(
        "/api/v1/goal-briefs",
        json={
            "message": "设计一个面向岗位的项目",
            "hint_intent": "create_plan",
        },
        headers=headers,
    )

    assert created.status_code == HTTPStatus.CREATED
    body = created.json()
    assert body["status"] == "awaiting_confirmation"
    assert body["duration_weeks"] == 5
    assert any("共 35 天" in assumption for assumption in body["assumptions"])
    assert any("不会安排到日期范围之外" in assumption for assumption in body["assumptions"])
    assert body["objective_type"] == "project"
    assert body["objective"] == "设计一个面向岗位的项目"
    assert repeated.json()["goal_brief_id"] == body["goal_brief_id"]

    me_before = await api_client.get("/api/v1/me", headers=bearer(token))
    assert me_before.json()["active_run"] is None
    assert me_before.json()["active_goal_brief"]["goal_brief_id"] == body["goal_brief_id"]

    confirmed = await api_client.post(
        f"/api/v1/goal-briefs/{body['goal_brief_id']}/confirm",
        json={"version": body["version"]},
        headers=bearer(token),
    )
    assert confirmed.status_code == HTTPStatus.ACCEPTED
    assert confirmed.json()["goal_brief"]["status"] == "confirmed"
    assert confirmed.json()["run"]["status"] == "pending"

    me_after = await api_client.get("/api/v1/me", headers=bearer(token))
    assert me_after.json()["active_goal_brief"] is None
    assert me_after.json()["active_run"]["run_id"] == confirmed.json()["run"]["run_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "objective_type", "expected_deliverable"),
    [
        ("从零规划求职准备", "career_plan", "阶段行动计划"),
        ("做一个能写进简历的项目", "project", "可运行的项目成果"),
        ("开始准备投递", "application", "投递与反馈跟踪表"),
        ("准备下一场面试", "interview", "模拟面试复盘"),
        ("学习 Python 转型 AI 后端", "skill_transition", "学习实践成果"),
    ],
)
async def test_goal_brief_uses_objective_specific_policy(
    api_client: AsyncClient,
    message: str,
    objective_type: str,
    expected_deliverable: str,
) -> None:
    token, _, _ = await guest_login(api_client)
    await api_client.put(
        "/api/v1/profile",
        json=profile_body(),
        headers={**bearer(token), "Idempotency-Key": f"profile-{objective_type}"},
    )

    response = await api_client.post(
        "/api/v1/goal-briefs",
        json={"message": message, "hint_intent": "create_plan"},
        headers={**bearer(token), "Idempotency-Key": f"brief-{objective_type}"},
    )

    assert response.status_code == HTTPStatus.CREATED
    body = response.json()
    assert body["status"] == "awaiting_confirmation"
    assert body["objective_type"] == objective_type
    assert body["objective"] == message
    assert expected_deliverable in body["deliverables"]
    assert not any("设计哪类项目" in question for question in body["questions"])


@pytest.mark.asyncio
async def test_goal_brief_blocks_high_risk_before_goal_provider(
    api_client: AsyncClient,
    api_application: FastAPI,
) -> None:
    class SpyProvider:
        method = "model"
        model_id = "must-not-be-called"

        def __init__(self) -> None:
            self.calls = 0

        async def extract(self, message: str) -> GoalExtraction:
            self.calls += 1
            return GoalExtraction(objective=message)

    provider = SpyProvider()
    api_application.dependency_overrides[get_goal_understanding_provider] = lambda: provider
    token, _, _ = await guest_login(api_client)

    response = await api_client.post(
        "/api/v1/goal-briefs",
        json={"message": "我想结束生命，帮我做计划", "hint_intent": "create_plan"},
        headers={**bearer(token), "Idempotency-Key": "unsafe-brief"},
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json()["error"]["code"] == "SAFETY_HIGH_RISK_INPUT"
    assert provider.calls == 0
