"""Tests for the developer repair-report endpoint.

The local test database is shared with a running Compose instance, so the
suite uses the same baseline/delta pattern as the usage-report tests.

Pins:
* Dev-role guarded: regular users get 403.
* A format-repair step with no fallback contributes one trigger + one
  success; a run with ``format_repair_failed`` contributes a failure.
* Business repair distinguishes attempted (step exists) from
  budget-declined (fallback without a step).
* Fallback-reason distribution includes the created reasons.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from http import HTTPStatus
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import TokenService
from app.models.agent_run import AgentRun, AgentStep
from app.models.user import User
from tests.test_profile_api import bearer, guest_login


def _run(user_id: UUID, *, fallback: str | None = None) -> AgentRun:
    now = datetime.now(UTC)
    return AgentRun(
        user_id=user_id,
        idempotency_key=f"repair-report-{uuid4()}",
        request_text="Create a plan",
        resolved_intent="create_plan",
        replan_mode="initial",
        status="degraded" if fallback else "completed",
        # Satisfies ck_agent_runs_degraded_result (degraded needs a
        # result_kind) and ck_agent_runs_completed_result.
        result_kind="clarification" if fallback else "interview_turn",
        graph_version=f"repair-test-{uuid4().hex[:8]}",
        config_snapshot_json={"provider": "mock"},
        model_id="mock-career-planner-v1",
        total_tokens_in=10,
        total_tokens_out=5,
        total_cost_cny=Decimal("0"),
        total_latency_ms=10,
        fallback_reason=fallback,
        error_code=None,
        deadline_at=now + timedelta(seconds=45),
        created_at=now,
        started_at=now,
        finished_at=now,
    )


def _step(run: AgentRun, *, prompt_version: str, sequence: int) -> AgentStep:
    now = datetime.now(UTC)
    return AgentStep(
        run_id=run.id,
        sequence=sequence,
        node_name="career_planning_agent",
        attempt=1,
        status="completed",
        prompt_version=prompt_version,
        model_id="mock-career-planner-v1",
        tokens_in=10,
        tokens_out=5,
        cost_cny=Decimal("0"),
        latency_ms=10,
        trace_data={},
        created_at=now,
        finished_at=now,
    )


def _repair(body: dict, kind: str) -> dict:
    return next(item for item in body["repairs"] if item["kind"] == kind)


@pytest.mark.asyncio
async def test_repair_report_counts_attempts_failures_and_declines(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    token, user_id_raw, _ = await guest_login(api_client)
    forbidden = await api_client.get(
        "/api/v1/dev/repair-report", headers=bearer(token)
    )
    assert forbidden.status_code == HTTPStatus.FORBIDDEN

    user = await db_session.get(User, UUID(user_id_raw))
    assert user is not None
    user.role = "dev"
    await db_session.flush()
    dev_token = TokenService(get_settings()).issue(user_id=user.id, role="dev")

    baseline = (
        await api_client.get("/api/v1/dev/repair-report", headers=bearer(dev_token))
    ).json()
    base_format = _repair(baseline, "format_repair")
    base_business = _repair(baseline, "business_repair")
    base_reasons = {
        item["reason"]: item["count"] for item in baseline["fallback_reasons"]
    }

    user_id = UUID(user_id_raw)

    # 1) Format repair attempted and succeeded (step, no fallback).
    ok_run = _run(user_id)
    # 2) Format repair attempted and failed (step + fallback reason).
    failed_run = _run(user_id, fallback="format_repair_failed")
    # 3) Business repair attempted and succeeded.
    biz_ok_run = _run(user_id)
    # 4) Business repair declined by budget (fallback, no step).
    declined_run = _run(user_id, fallback="business_repair_budget_insufficient")
    db_session.add_all([ok_run, failed_run, biz_ok_run, declined_run])
    await db_session.flush()
    db_session.add_all(
        [
            _step(ok_run, prompt_version="mock_format_repair_v1", sequence=1),
            _step(failed_run, prompt_version="mock_format_repair_v1", sequence=1),
            _step(biz_ok_run, prompt_version="mock_business_repair_v1", sequence=1),
        ]
    )
    await db_session.flush()

    body = (
        await api_client.get("/api/v1/dev/repair-report", headers=bearer(dev_token))
    ).json()

    fmt = _repair(body, "format_repair")
    assert fmt["triggered"] - base_format["triggered"] == 2
    assert fmt["succeeded"] - base_format["succeeded"] == 1
    assert fmt["failed_after_attempt"] - base_format["failed_after_attempt"] == 1
    assert fmt["declined_by_budget"] == base_format["declined_by_budget"]

    biz = _repair(body, "business_repair")
    assert biz["triggered"] - base_business["triggered"] == 1
    assert biz["succeeded"] - base_business["succeeded"] == 1
    assert biz["failed_after_attempt"] == base_business["failed_after_attempt"]
    assert (
        biz["declined_by_budget"] - base_business["declined_by_budget"] == 1
    )

    reasons = {item["reason"]: item["count"] for item in body["fallback_reasons"]}
    assert (
        reasons.get("format_repair_failed", 0)
        - base_reasons.get("format_repair_failed", 0)
        == 1
    )
    assert (
        reasons.get("business_repair_budget_insufficient", 0)
        - base_reasons.get("business_repair_budget_insufficient", 0)
        == 1
    )
