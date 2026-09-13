"""Stage 2 Agent Run HTTP contract and identity-isolation tests."""

from http import HTTPStatus
from typing import cast

import pytest
from httpx import AsyncClient

from tests.test_profile_api import bearer, guest_login, profile_body


async def create_confirmed_run(
    api_client: AsyncClient,
    token: str,
    *,
    key: str,
) -> dict[str, object]:
    brief = await api_client.post(
        "/api/v1/goal-briefs",
        json={"message": "制定一份求职准备计划", "hint_intent": "create_plan"},
        headers={**bearer(token), "Idempotency-Key": f"{key}-brief"},
    )
    assert brief.status_code == HTTPStatus.CREATED
    confirmed = await api_client.post(
        f"/api/v1/goal-briefs/{brief.json()['goal_brief_id']}/confirm",
        json={"version": brief.json()["version"]},
        headers=bearer(token),
    )
    assert confirmed.status_code == HTTPStatus.ACCEPTED
    return cast(dict[str, object], confirmed.json()["run"])


@pytest.mark.asyncio
async def test_public_creation_is_closed_and_confirmed_run_can_be_managed(
    api_client: AsyncClient,
) -> None:
    token, _, _ = await guest_login(api_client)
    await api_client.put(
        "/api/v1/profile",
        json=profile_body(),
        headers={**bearer(token), "Idempotency-Key": "stage2-profile"},
    )
    bypass = await api_client.post(
        "/api/v1/agent-runs",
        json={"message": "帮我制定未来五周计划", "hint_intent": "create_plan"},
        headers={**bearer(token), "Idempotency-Key": "bypass"},
    )
    run = await create_confirmed_run(api_client, token, key="stage2-run")
    run_id = run["run_id"]
    fetched = await api_client.get(f"/api/v1/agent-runs/{run_id}", headers=bearer(token))
    query_token_attempt = await api_client.get(
        f"/api/v1/agent-runs/{run_id}/events",
        params={"access_token": token},
    )
    cancelled = await api_client.post(
        f"/api/v1/agent-runs/{run_id}/cancel",
        json={"reason": "user_abort"},
        headers={**bearer(token), "Idempotency-Key": "stage2-cancel"},
    )
    cancel_key_reused = await api_client.post(
        f"/api/v1/agent-runs/{run_id}/cancel",
        json={"reason": "different_reason"},
        headers={**bearer(token), "Idempotency-Key": "stage2-cancel"},
    )

    assert bypass.status_code == HTTPStatus.NOT_FOUND
    assert fetched.status_code == HTTPStatus.OK
    assert fetched.json()["status"] == "pending"
    assert query_token_attempt.status_code == HTTPStatus.UNAUTHORIZED
    assert cancelled.status_code == HTTPStatus.ACCEPTED
    assert cancelled.json()["cancel_requested"] is True
    assert cancel_key_reused.status_code == HTTPStatus.CONFLICT
    assert cancel_key_reused.json()["error"]["code"] == "STATE_IDEMPOTENCY_KEY_REUSED"


@pytest.mark.asyncio
async def test_agent_run_api_closes_public_post_and_hides_other_users(
    api_client: AsyncClient,
) -> None:
    token_a, user_a, _ = await guest_login(api_client)
    token_b, _, _ = await guest_login(api_client)
    await api_client.put(
        "/api/v1/profile",
        json=profile_body(),
        headers={**bearer(token_a), "Idempotency-Key": "isolated-profile"},
    )
    invalid = await api_client.post(
        "/api/v1/agent-runs",
        json={"message": "制定计划", "user_id": user_a},
        headers={**bearer(token_a), "Idempotency-Key": "invalid-user-id"},
    )
    assert invalid.status_code == HTTPStatus.NOT_FOUND
    created = await create_confirmed_run(api_client, token_a, key="isolated-run")
    hidden = await api_client.get(
        f"/api/v1/agent-runs/{created['run_id']}",
        headers=bearer(token_b),
    )
    hidden_events = await api_client.get(
        f"/api/v1/agent-runs/{created['run_id']}/events",
        headers=bearer(token_b),
    )
    no_plan = await api_client.get("/api/v1/plans/active", headers=bearer(token_b))
    no_tasks = await api_client.get("/api/v1/tasks", headers=bearer(token_b))

    assert hidden.status_code == HTTPStatus.NOT_FOUND
    assert hidden_events.status_code == HTTPStatus.NOT_FOUND
    assert no_plan.status_code == HTTPStatus.NOT_FOUND
    assert no_tasks.status_code == HTTPStatus.OK
    assert no_tasks.json()["items"] == []
