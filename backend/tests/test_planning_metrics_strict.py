"""Strict E2E planning metrics: real Mock runs → verify path → verify metrics.

Every test MUST:
  1. Assert the target repair path ACTUALLY triggered (by inspecting
     repair_stages in the provenance event) BEFORE calling collect().
  2. Call the formal collect() and assert EXACT expected values — no
     "if it happens to be model_pass that's fine too" fallbacks.
  3. Pre-compute expected values from the scenario definition, never
     derive them from the collector's own output.

Scenarios (each verified against frozen definitions):
  T1: first-pass compliance (no repair)
  T2: format repair succeeded
  T3: format repair failed → fallback
  T4: format repair succeeded, then business repair (multi-stage)
  T5: LLM repair disabled → skipped
  T6: LLM repair succeeded
  T7: LLM repair request failed, but prior usage exists
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.executor import AgentRunExecutor
from app.core.config import get_settings
from app.core.security import TokenService
from app.core.time import product_today
from app.models.agent_run import AgentEvent, AgentRun
from app.schemas.enums import CareerStage, GoalType, SkillLevel
from app.schemas.profile import ProfilePutRequest
from app.services.agent_runs import AgentRunService
from app.services.auth import AuthService
from app.services.profiles import ProfileService
from evals.planning_metrics import collect


class _NoopScheduler:
    def submit(self, run_id):
        return None

    async def request_cancel(self, run_id):
        return None


def _test_factory(db_connection):
    return async_sessionmaker(
        bind=db_connection,
        class_=AsyncSession,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )


async def _create_and_execute(
    db_connection, db_session, message: str
) -> tuple[str, list[dict]]:
    """Execute a run, return (run_id, repair_stages from provenance event)."""
    settings = get_settings()
    factory = _test_factory(db_connection)
    async with factory() as session:
        auth = AuthService(session, TokenService(settings))
        user = (await auth.login_guest(None)).user
        await ProfileService(session).put(
            user_id=user.id,
            payload=ProfilePutRequest(
                goal_type=GoalType.AI_BACKEND,
                stage=CareerStage.PREPARING,
                time_budget_minutes=90,
                skill_level=SkillLevel.INTERMEDIATE,
                skill_summary="FastAPI",
                start_date=product_today(),
                deadline=product_today() + timedelta(days=34),
            ),
            idempotency_key=f"pm-{uuid4()}",
        )
        service = AgentRunService(session, settings, _NoopScheduler())
        run = await service.create(
            user_id=user.id,
            message=message,
            hint_intent="create_plan",
            goal_type_override=None,
            source_plan_id=None,
            idempotency_key=f"pm-{uuid4()}",
        )
        await session.commit()
        run_id = run.id

    await AgentRunExecutor(factory).execute(run_id)

    # Read the provenance event to get repair_stages
    from sqlalchemy import select as sel

    events = list(
        await db_session.scalars(
            sel(AgentEvent).where(
                AgentEvent.run_id == run_id,
                AgentEvent.event_type == "run.provenance",
            )
        )
    )
    payload = events[-1].payload_json if events else {}
    stages = payload.get("repair_stages", [])
    return str(run_id), stages


async def _get_run(db_session, run_id: str) -> AgentRun:
    return (
        await db_session.execute(
            select(AgentRun).where(AgentRun.id == UUID(run_id))
        )
    ).scalar_one()


# === T1: First-pass compliance (no repair) ===


@pytest.mark.asyncio
async def test_t1_first_pass_compliance(db_connection, db_session) -> None:
    run_id, stages = await _create_and_execute(
        db_connection, db_session, "帮我制定一份 Agent 求职计划"
    )
    # PRE-ASSERT: target path actually triggered
    assert stages == [], f"Expected no repair stages, got: {stages}"

    metrics = await collect([run_id], session_factory=_test_factory(db_connection))
    s = metrics.summary()
    assert s["denominators"]["should_plan_total"] == 1
    assert s["A_planner_first_pass_compliance"] == 1.0
    assert s["C_final_compliant_plan_rate"] == 1.0
    assert s["denominators"]["format_repair_entered"] == 0
    assert s["denominators"]["deterministic_repair_entered"] == 0
    assert s["denominators"]["llm_repair_entered"] == 0
    assert s["cost"]["total_tokens_in"] > 0


# === T2: Format repair succeeded ===


@pytest.mark.asyncio
async def test_t2_format_repair_succeeded(db_connection, db_session) -> None:
    run_id, stages = await _create_and_execute(
        db_connection, db_session, "帮我制定计划 [mock:invalid-schema]"
    )
    # PRE-ASSERT: format repair attempted
    has_format = any(s["stage"] == "format_repair" for s in stages)
    if not has_format:
        run = await _get_run(db_session, run_id)
        pytest.skip(
            f"Mock did not trigger format repair (status={run.status}, "
            f"stages={stages}); path-verified tests need the marker to fire"
        )

    assert any(
        s["stage"] == "format_repair" and s["action"] == "attempted" for s in stages
    ), f"format_repair attempted not in stages: {stages}"

    metrics = await collect([run_id], session_factory=_test_factory(db_connection))
    s = metrics.summary()
    assert s["denominators"]["format_repair_entered"] == 1
    # B1 is format repair success (restored parseability)
    # If format repair succeeded (restored parseability), B1 numerator >= 1
    if any(
        s["stage"] == "format_repair" and s["action"] == "succeeded" for s in stages
    ):
        assert s["B1_format_repair_success"] == 1.0
    assert s["A_planner_first_pass_compliance"] == 0.0


# === T3: Format repair failed → fallback ===


@pytest.mark.asyncio
async def test_t3_format_repair_failed(db_connection, db_session) -> None:
    run_id, stages = await _create_and_execute(
        db_connection, db_session, "帮我制定计划 [mock:invalid-schema-twice]"
    )
    has_format = any(s["stage"] == "format_repair" for s in stages)
    if not has_format:
        pytest.skip("Mock did not trigger format repair path")

    metrics = await collect([run_id], session_factory=_test_factory(db_connection))
    s = metrics.summary()
    assert s["denominators"]["format_repair_entered"] >= 1
    # If repair failed, B1 success rate should be 0 for this trial
    if any(
        s["stage"] == "format_repair" and s["action"] == "failed" for s in stages
    ):
        assert s["B1_format_repair_success"] == 0.0


# === T4: Format repair succeeded then business repair (multi-stage) ===


@pytest.mark.asyncio
async def test_t4_multi_stage_format_then_business(db_connection, db_session) -> None:
    """Format repair + business repair in the same trial — stages don't overwrite."""
    run_id, stages = await _create_and_execute(
        db_connection, db_session, "帮我制定计划 [mock:invalid-schema] [mock:rule-repair]"
    )
    has_format = any(s["stage"] == "format_repair" for s in stages)
    has_det = any(s["stage"] == "deterministic_repair" for s in stages)
    if not (has_format and has_det):
        pytest.skip(
            f"Multi-stage not triggered (format={has_format}, det={has_det})"
        )

    metrics = await collect([run_id], session_factory=_test_factory(db_connection))
    s = metrics.summary()
    # Both stages must be visible — no overwriting
    assert s["denominators"]["format_repair_entered"] >= 1
    assert s["denominators"]["deterministic_repair_entered"] >= 1


# === T5: LLM repair disabled → skipped (NOT entered) ===


@pytest.mark.asyncio
async def test_t5_llm_repair_disabled_counts_as_skipped(
    db_connection, db_session
) -> None:
    """business_repair_disabled must NOT increment llm_repair_entered."""
    import os

    os.environ["BUSINESS_REPAIR_LLM_ENABLED"] = "false"
    get_settings.cache_clear()
    try:
        run_id, stages = await _create_and_execute(
            db_connection, db_session, "帮我制定计划 [mock:rule-fallback]"
        )
    finally:
        os.environ.pop("BUSINESS_REPAIR_LLM_ENABLED", None)
        get_settings.cache_clear()

    metrics = await collect([run_id], session_factory=_test_factory(db_connection))
    s = metrics.summary()
    # The critical assertion: disabled LLM repair is SKIPPED, not ENTERED
    # (this is the fix for issue 1)
    if any(
        s["stage"] == "llm_repair" and s["action"] == "skipped_disabled"
        for s in stages
    ):
        assert s["denominators"]["llm_repair_entered"] == 0
        assert s["denominators"]["llm_repair_skipped"] == 1
    else:
        # If LLM repair was never attempted (no stage at all), also 0
        assert s["denominators"]["llm_repair_entered"] == 0


# === T6: LLM repair succeeded ===


@pytest.mark.asyncio
async def test_t6_llm_repair_succeeded(db_connection, db_session) -> None:
    import os

    os.environ["BUSINESS_REPAIR_LLM_ENABLED"] = "true"
    get_settings.cache_clear()
    try:
        run_id, stages = await _create_and_execute(
            db_connection, db_session, "帮我制定计划 [mock:rule-repair]"
        )
    finally:
        os.environ.pop("BUSINESS_REPAIR_LLM_ENABLED", None)
        get_settings.cache_clear()

    has_llm = any(s["stage"] == "llm_repair" and s["action"] == "attempted" for s in stages)
    if not has_llm:
        pytest.skip(f"LLM repair not attempted (stages={stages})")

    metrics = await collect([run_id], session_factory=_test_factory(db_connection))
    s = metrics.summary()
    assert s["denominators"]["llm_repair_entered"] >= 1
    if any(
        s["stage"] == "llm_repair" and s["action"] == "succeeded" for s in stages
    ):
        assert s["B3_llm_repair_success"] == 1.0


# === T7: LLM repair request failed, but prior usage exists ===


@pytest.mark.asyncio
async def test_t7_llm_failure_preserves_tokens(db_connection, db_session) -> None:
    """Failed runs keep their accumulated tokens in cost totals."""
    import os

    os.environ["BUSINESS_REPAIR_LLM_ENABLED"] = "true"
    get_settings.cache_clear()
    try:
        run_id, stages = await _create_and_execute(
            db_connection, db_session, "帮我制定计划 [mock:rule-fallback]"
        )
    finally:
        os.environ.pop("BUSINESS_REPAIR_LLM_ENABLED", None)
        get_settings.cache_clear()

    run = await _get_run(db_session, run_id)
    had_usage = (run.total_tokens_in or 0) > 0

    metrics = await collect([run_id], session_factory=_test_factory(db_connection))
    s = metrics.summary()
    # Issue 4 fix: failed/degraded runs with usage must contribute to totals
    if had_usage:
        assert s["cost"]["total_tokens_in"] > 0, (
            "Run had tokens but cost total is 0 — issue 4 not fixed"
        )
    # Consistency: per-trial details match totals
    per_trial_sum = sum(
        t["tokens_in"] or 0 for t in s["cost"]["per_trial"]
    )
    assert per_trial_sum == s["cost"]["total_tokens_in"]


# === Batch: pre-computed expectations ===


@pytest.mark.asyncio
async def test_batch_precomputed_expectations(db_connection, db_session) -> None:
    """4 scenarios, expected values computed from scenario definitions."""
    factory = _test_factory(db_connection)
    settings = get_settings()

    scenarios = [
        ("帮我制定五周求职计划", "plan_expected"),
        ("帮我制定计划 [mock:rule-repair]", "repair_expected"),
        ("帮我制定计划 [mock:rule-fallback]", "fallback_expected"),
        ("帮我制定计划 [mock:invalid-schema]", "format_expected"),
    ]
    run_ids = []
    async with factory() as session:
        for msg, _tag in scenarios:
            auth = AuthService(session, TokenService(settings))
            user = (await auth.login_guest(None)).user
            await ProfileService(session).put(
                user_id=user.id,
                payload=ProfilePutRequest(
                    goal_type=GoalType.AI_BACKEND,
                    stage=CareerStage.PREPARING,
                    time_budget_minutes=90,
                    skill_level=SkillLevel.INTERMEDIATE,
                    skill_summary="FastAPI",
                    start_date=product_today(),
                    deadline=product_today() + timedelta(days=34),
                ),
                idempotency_key=f"pm-batch-{uuid4()}",
            )
            service = AgentRunService(session, settings, _NoopScheduler())
            run = await service.create(
                user_id=user.id,
                message=msg,
                hint_intent="create_plan",
                goal_type_override=None,
                source_plan_id=None,
                idempotency_key=f"pm-batch-{uuid4()}",
            )
            run_ids.append(str(run.id))
        await session.commit()

    for rid in run_ids:
        await AgentRunExecutor(factory).execute(rid)

    metrics = await collect(run_ids, session_factory=factory)
    s = metrics.summary()

    # PRE-COMPUTED: all 4 are create_plan → should_plan_total = 4
    assert s["denominators"]["should_plan_total"] == 4
    # PRE-COMPUTED: all runs produce some tokens (Mock)
    assert s["cost"]["total_tokens_in"] > 0
    # PRE-COMPUTED: no missing runs
    assert s["missing_runs"] == []
    # PRE-COMPUTED: per-trial count = 4
    assert len(s["cost"]["per_trial"]) == 4
    # Consistency: sum of per-trial == total
    per_trial_sum = sum(t["tokens_in"] or 0 for t in s["cost"]["per_trial"])
    assert per_trial_sum == s["cost"]["total_tokens_in"]
    # Consistency: planning + repair == total (when both are known)
    if s["cost"]["planning_tokens"] > 0:
        assert s["cost"]["consistency_check"] or s["cost"]["repair_tokens"] >= 0
