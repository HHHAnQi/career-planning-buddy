"""Stage 5 developer Trace, fixture Replay, and Eval acceptance tests."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from http import HTTPStatus
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import TokenService
from app.models.agent_run import AgentEvent, AgentRun, AgentStep, ToolCall
from app.models.user import User
from evals.runner import GRADERS, load_cases, run_evaluation
from tests.test_profile_api import bearer, guest_login


async def _terminal_run(session: AsyncSession, user_id: UUID) -> AgentRun:
    now = datetime.now(UTC)
    run = AgentRun(
        user_id=user_id,
        idempotency_key="stage5-trace-source",
        request_text="Create a plan without leaking secret data",
        resolved_intent="create_plan",
        replan_mode="initial",
        status="degraded",
        result_kind="clarification",
        result_payload_json={
            "questions": ["Which direction?"],
            "slot_names": ["goal_type"],
            "hint_options": {"goal_type": ["agent_app"]},
            "reason": "profile_incomplete",
        },
        graph_version="stage5-v1",
        input_snapshot_json={"profile": {"user_id": str(user_id)}, "jwt": "private"},
        config_snapshot_json={
            "provider": "mock",
            "model_alias": "mock-career-planner-v1",
            "api_key": "must-not-escape",
        },
        model_id="mock-career-planner-v1",
        total_tokens_in=0,
        total_tokens_out=0,
        total_cost_cny=Decimal("0"),
        total_latency_ms=2,
        fallback_reason="profile_incomplete",
        deadline_at=now + timedelta(seconds=45),
        created_at=now,
        started_at=now,
        finished_at=now,
    )
    session.add(run)
    await session.flush()
    step = AgentStep(
        run_id=run.id,
        sequence=1,
        node_name="intent_router",
        attempt=1,
        status="completed",
        trace_data={"authorization": "Bearer private"},
        latency_ms=1,
        created_at=now,
        finished_at=now,
    )
    session.add(step)
    session.add_all(
        [
            AgentEvent(
                run_id=run.id,
                sequence=1,
                event_type="run.created",
                payload_json={"status": "pending"},
                created_at=now,
            ),
            AgentEvent(
                run_id=run.id,
                sequence=2,
                event_type="run.degraded",
                payload_json={"status": "degraded", "result_kind": "clarification"},
                created_at=now,
            ),
        ]
    )
    run.next_event_sequence = 3
    run.next_step_sequence = 2
    await session.flush()
    return run


@pytest.mark.asyncio
async def test_dev_trace_is_role_guarded_redacted_and_terminal_checked(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    token, user_id_raw, _ = await guest_login(api_client)
    run = await _terminal_run(db_session, UUID(user_id_raw))
    forbidden = await api_client.get("/api/v1/dev/runs", headers=bearer(token))

    user = await db_session.get(User, UUID(user_id_raw))
    assert user is not None
    user.role = "dev"
    await db_session.flush()
    dev_token = TokenService(get_settings()).issue(user_id=user.id, role="dev")
    listed = await api_client.get("/api/v1/dev/runs", headers=bearer(dev_token))
    detailed = await api_client.get(f"/api/v1/dev/runs/{run.id}", headers=bearer(dev_token))

    assert forbidden.status_code == HTTPStatus.FORBIDDEN
    assert listed.status_code == HTTPStatus.OK
    assert any(item["run_id"] == str(run.id) for item in listed.json()["items"])
    body = detailed.json()
    assert body["terminal_invariant"] == {
        "terminal_count": 1,
        "terminal_is_last": True,
        "valid": True,
    }
    serialized = detailed.text
    assert "must-not-escape" not in serialized
    assert "Bearer private" not in serialized
    assert "[REDACTED]" in serialized
    assert user_id_raw not in body["run"]["user_ref"]


@pytest.mark.asyncio
async def test_legacy_trace_clone_is_explicit_and_does_not_mutate_source(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    _token, user_id_raw, _ = await guest_login(api_client)
    user = await db_session.get(User, UUID(user_id_raw))
    assert user is not None
    user.role = "dev"
    source = await _terminal_run(db_session, user.id)
    await db_session.flush()
    dev_token = TokenService(get_settings()).issue(user_id=user.id, role="dev")

    response = await api_client.post(
        f"/api/v1/dev/runs/{source.id}/replay",
        json={"tool_mode": "fixture"},
        headers=bearer(dev_token),
    )
    await db_session.refresh(source)
    replay = await db_session.get(AgentRun, UUID(response.json()["run_id"]))

    assert response.status_code == HTTPStatus.ACCEPTED
    assert response.json()["deterministic"] is True
    assert response.json()["execution_kind"] == "legacy_trace_clone"
    assert replay is not None
    assert replay.replay_of_run_id == source.id
    assert replay.config_snapshot_json["provider"] == "mock"
    assert replay.config_snapshot_json["execution_kind"] == "legacy_trace_clone"
    assert replay.result_payload_json == source.result_payload_json
    assert source.replay_of_run_id is None


@pytest.mark.asyncio
async def test_replay_rejects_missing_tool_fixture(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    _token, user_id_raw, _ = await guest_login(api_client)
    user = await db_session.get(User, UUID(user_id_raw))
    assert user is not None
    user.role = "dev"
    source = await _terminal_run(db_session, user.id)
    step = AgentStep(
        run_id=source.id,
        sequence=2,
        node_name="career_planning_agent",
        attempt=1,
        status="completed",
        trace_data={},
    )
    db_session.add(step)
    await db_session.flush()
    db_session.add(
        ToolCall(
            run_id=source.id,
            step_id=step.id,
            tool_name="rag_retrieve",
            tool_contract_version="1.0",
            round=1,
            args_json={"query": "fixture", "goal_type": "agent_app", "limit": 3},
            args_hash="0" * 64,
            result_json=None,
            provider="mock",
            success=False,
        )
    )
    await db_session.flush()
    token = TokenService(get_settings()).issue(user_id=user.id, role="dev")

    response = await api_client.post(
        f"/api/v1/dev/runs/{source.id}/replay",
        json={"tool_mode": "fixture"},
        headers=bearer(token),
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json()["error"]["code"] == "REPLAY_FIXTURE_MISSING"


@pytest.mark.asyncio
async def test_fixed_eval_dataset_runs_all_graders_without_network() -> None:
    report = await run_evaluation(persist=False)

    assert len(load_cases()) == 30
    assert report["provider"] == "mock"
    assert report["deterministic"] is True
    assert report["case_count"] == 30
    assert report["passed_cases"] == 30
    rates = report["grader_pass_rates"]
    assert isinstance(rates, dict)
    assert set(rates) == set(GRADERS)
    assert "snapshot_replay" not in rates
    assert all(value == 1.0 for value in rates.values())


@pytest.mark.asyncio
async def test_dev_eval_api_lists_runs_and_reads_persisted_report(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    _token, user_id_raw, _ = await guest_login(api_client)
    user = await db_session.get(User, UUID(user_id_raw))
    assert user is not None
    user.role = "dev"
    await db_session.flush()
    token = TokenService(get_settings()).issue(user_id=user.id, role="dev")

    datasets = await api_client.get("/api/v1/dev/evals/datasets", headers=bearer(token))
    started = await api_client.post(
        "/api/v1/dev/evals/experiments",
        json={"dataset_id": "stage5-v1", "case_limit": 1},
        headers=bearer(token),
    )
    fetched = await api_client.get(
        f"/api/v1/dev/evals/experiments/{started.json()['experiment_id']}",
        headers=bearer(token),
    )

    assert datasets.status_code == HTTPStatus.OK
    assert datasets.json()["items"][0]["case_count"] == 30
    assert started.status_code == HTTPStatus.ACCEPTED
    assert fetched.status_code == HTTPStatus.OK
    assert fetched.json()["report"]["case_count"] == 1
