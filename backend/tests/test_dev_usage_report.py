"""Tests for the developer usage-report endpoint.

The local test database is shared with a running Compose instance, so the
suite uses a baseline/delta pattern: capture one report before creating
test data, then assert the exact deltas afterwards. Slices keyed by a
per-test unique graph_version are asserted exactly.

Pins:
* Dev-role guarded: regular users get 403.
* Runs inside the window contribute exact status/cost/token deltas;
  runs older than the window are excluded.
* Nearest-rank percentile helper behavior (unit level).
* Per-graph slices with a unique graph_version aggregate exactly.
* Provider-call rows roll up into per-kind health weighted by count.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from http import HTTPStatus
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import TokenService
from app.models.agent_run import AgentRun
from app.models.provider_call import ProviderCall
from app.models.user import User
from app.services.dev import _percentile
from tests.test_profile_api import bearer, guest_login


def _run(
    user_id: UUID,
    *,
    graph_version: str,
    status: str = "completed",
    cost: str = "0.1",
    latency_ms: int = 1000,
    fallback: str | None = None,
    created_at: datetime | None = None,
    index: int = 0,
) -> AgentRun:
    now = created_at or datetime.now(UTC)
    return AgentRun(
        user_id=user_id,
        idempotency_key=f"usage-report-{uuid4()}",
        request_text="Create a plan",
        resolved_intent="create_plan",
        replan_mode="initial",
        status=status,
        # Satisfies ck_agent_runs_*: completed ⇒ interview_turn, degraded ⇒
        # clarification + fallback, failed ⇒ no result + error_code.
        result_kind=(
            "interview_turn"
            if status == "completed"
            else "clarification"
            if status == "degraded"
            else None
        ),
        graph_version=graph_version,
        config_snapshot_json={"provider": "mock"},
        model_id="mock-career-planner-v1",
        total_tokens_in=100 + index,
        total_tokens_out=50 + index,
        total_cost_cny=Decimal(cost),
        total_latency_ms=latency_ms,
        fallback_reason=fallback,
        error_code="AGENT_EXECUTION_FAILED" if status == "failed" else None,
        deadline_at=now + timedelta(seconds=45),
        created_at=now,
        started_at=now,
        finished_at=now,
    )


def _provider_call(
    run_id: UUID,
    *,
    kind: str = "llm",
    status: str = "ok",
    latency_ms: int = 200,
    sequence: int = 1,
) -> ProviderCall:
    return ProviderCall(
        run_id=run_id,
        sequence=sequence,
        provider_kind=kind,
        provider_method="generate_plan" if kind == "llm" else "search",
        logical_call_index=1,
        retry_attempt=0,
        request_projection={"op": "plan"},
        request_projection_hash="a" * 64,
        status=status,
        error_code="PROVIDER_TIMEOUT" if status == "error" else None,
        tokens_in=10 if kind == "llm" else None,
        tokens_out=5 if kind == "llm" else None,
        latency_ms=latency_ms,
        model_id="mock-career-planner-v1" if kind == "llm" else None,
    )


def _kind(report: dict, name: str) -> dict:
    return next(item for item in report["provider_kinds"] if item["provider_kind"] == name)


@pytest.mark.asyncio
async def test_usage_report_aggregates_exact_deltas(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    token, user_id_raw, _ = await guest_login(api_client)
    user_id = UUID(user_id_raw)

    forbidden = await api_client.get("/api/v1/dev/usage-report", headers=bearer(token))
    assert forbidden.status_code == HTTPStatus.FORBIDDEN

    user = await db_session.get(User, user_id)
    assert user is not None
    user.role = "dev"
    await db_session.flush()
    dev_token = TokenService(get_settings()).issue(user_id=user.id, role="dev")

    baseline_response = await api_client.get("/api/v1/dev/usage-report", headers=bearer(dev_token))
    assert baseline_response.status_code == HTTPStatus.OK
    baseline = baseline_response.json()

    graph_version = f"usage-report-test-{uuid4().hex[:8]}"
    now = datetime.now(UTC)
    runs = [
        _run(user_id, graph_version=graph_version, cost="0.2", latency_ms=1000, index=0),
        _run(user_id, graph_version=graph_version, cost="0.3", latency_ms=2000, index=1),
        _run(
            user_id,
            graph_version=graph_version,
            status="degraded",
            cost="0.1",
            latency_ms=3000,
            fallback="format_repair_exhausted",
            index=2,
        ),
        _run(
            user_id, graph_version=graph_version, status="failed", cost="0", latency_ms=0, index=3
        ),
        # Outside the default 30-day window: must not contribute.
        _run(
            user_id,
            graph_version=graph_version,
            cost="9.9",
            latency_ms=99_999,
            created_at=now - timedelta(days=31),
            index=4,
        ),
    ]
    db_session.add_all(runs)
    await db_session.flush()
    db_session.add_all(
        [
            _provider_call(runs[0].id, kind="llm", latency_ms=200, sequence=1),
            _provider_call(runs[0].id, kind="llm", status="error", latency_ms=600, sequence=2),
            _provider_call(runs[1].id, kind="search", latency_ms=300, sequence=1),
        ]
    )
    await db_session.flush()

    response = await api_client.get("/api/v1/dev/usage-report", headers=bearer(dev_token))
    assert response.status_code == HTTPStatus.OK
    body = response.json()

    base_totals = baseline["totals"]
    totals = body["totals"]
    assert totals["run_count"] - base_totals["run_count"] == 4
    assert totals["completed_count"] - base_totals["completed_count"] == 2
    assert totals["degraded_count"] - base_totals["degraded_count"] == 1
    assert totals["failed_count"] - base_totals["failed_count"] == 1
    assert totals["fallback_count"] - base_totals["fallback_count"] == 1
    assert Decimal(totals["total_cost_cny"]) - Decimal(base_totals["total_cost_cny"]) == Decimal(
        "0.6"
    )
    assert totals["total_tokens_in"] - base_totals["total_tokens_in"] == 100 + 101 + 102 + 103
    assert totals["total_tokens_out"] - base_totals["total_tokens_out"] == 50 + 51 + 52 + 53

    # The unique graph slice is isolated and exact.
    slice_row = next(g for g in body["graphs"] if g["graph_version"] == graph_version)
    assert slice_row["model_id"] == "mock-career-planner-v1"
    assert slice_row["run_count"] == 4
    assert Decimal(slice_row["total_cost_cny"]) == Decimal("0.6")
    assert slice_row["avg_latency_ms"] == (1000 + 2000 + 3000 + 0) // 4

    # Today's daily point gains exactly these 4 runs.
    today = datetime.now(UTC).date().isoformat()
    base_today = next(
        (d for d in baseline["daily"] if d["date"] == today),
        {"run_count": 0, "total_cost_cny": "0"},
    )
    new_today = next(d for d in body["daily"] if d["date"] == today)
    assert new_today["run_count"] - base_today["run_count"] == 4
    assert Decimal(new_today["total_cost_cny"]) - Decimal(base_today["total_cost_cny"]) == Decimal(
        "0.6"
    )

    # Provider kinds: exact call/error deltas and count-weighted latency.
    base_llm = _kind(baseline, "llm")
    new_llm = _kind(body, "llm")
    assert new_llm["call_count"] - base_llm["call_count"] == 2
    assert new_llm["error_count"] - base_llm["error_count"] == 1
    base_rows = await db_session.execute(
        select(ProviderCall.latency_ms).where(ProviderCall.provider_kind == "llm")
    )
    base_llm_total = sum(int(row[0]) for row in base_rows) - 200 - 600
    expected_llm_avg = (base_llm_total + 200 + 600) // new_llm["call_count"]
    # The report now aggregates exact per-bucket latency sums, so the
    # weighted average is exact (integer division only).
    assert new_llm["avg_latency_ms"] == expected_llm_avg

    base_search = _kind(baseline, "search")
    new_search = _kind(body, "search")
    assert new_search["call_count"] - base_search["call_count"] == 1
    assert new_search["error_count"] == base_search["error_count"]


def test_percentile_nearest_rank() -> None:
    assert _percentile([], 0.95) == 0
    assert _percentile([100], 0.5) == 100
    assert _percentile([100, 200, 300], 0.5) == 200
    assert _percentile([100, 200, 300], 0.95) == 300
    assert _percentile(list(range(1, 101)), 0.5) == 50
    assert _percentile(list(range(1, 101)), 0.95) == 95
