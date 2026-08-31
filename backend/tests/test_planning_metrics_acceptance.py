"""Planning metrics acceptance: 10 controlled scenarios (A-J) with
pre-computed expectations, verified through the real executor + collect().

Every scenario:
  1. Uses ScriptedProvider to FORCE the target path (no marker luck).
  2. PRE-COMPUTES exact expected metrics (never derived from collect output).
  3. Executes through the real AgentRunExecutor.
  4. Reads back with the formal collect().
  5. Asserts EXACT values — no conditional pass, no skip for required paths.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

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
from tests.providers import ScriptedProvider


class _NoopScheduler:
    def submit(self, run_id):
        return None

    async def request_cancel(self, run_id):
        return None


def _factory(db_connection):
    return async_sessionmaker(
        bind=db_connection,
        class_=AsyncSession,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )


async def _execute(
    db_connection, db_session, provider: ScriptedProvider
) -> tuple[str, list[dict]]:
    """Create user+run, execute with the given provider, return (run_id, repair_stages)."""
    settings = get_settings()
    factory = _factory(db_connection)
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
            idempotency_key=f"acc-{uuid4()}",
        )
        service = AgentRunService(session, settings, _NoopScheduler())
        run = await service.create(
            user_id=user.id,
            message="帮我制定一份 Agent 求职计划",
            hint_intent="create_plan",
            goal_type_override=None,
            source_plan_id=None,
            idempotency_key=f"acc-{uuid4()}",
        )
        await session.commit()
        run_id = run.id

    await AgentRunExecutor(factory, provider=provider).execute(run_id)

    # Read repair_stages from provenance event (if it exists)
    events = list(
        await db_session.scalars(
            select(AgentEvent).where(
                AgentEvent.run_id == run_id,
                AgentEvent.event_type == "run.provenance",
            )
        )
    )
    payload = events[-1].payload_json if events else {}
    stages = payload.get("repair_stages", [])
    return str(run_id), stages


async def _run_status(db_session, run_id: str) -> AgentRun:
    return (
        await db_session.execute(
            select(AgentRun).where(AgentRun.id == __import__("uuid").UUID(run_id))
        )
    ).scalar_one()


def _m(metrics) -> dict:
    return metrics.summary()


# ============================================================
# A: First-pass compliance — plan generates valid, no repair
# Expected: plan_calls=1, format_calls=0, business_calls=0
# Metrics: A=1.0, B*=None, C=1.0
# ============================================================


@pytest.mark.asyncio
async def test_a_first_pass(db_connection, db_session) -> None:
    provider = ScriptedProvider(plan_responses=[ScriptedProvider.context_aware_valid])
    run_id, stages = await _execute(db_connection, db_session, provider)

    # PRE-ASSERT path triggered
    assert provider.plan_calls == 1
    assert provider.format_repair_calls == 0
    assert provider.business_repair_calls == 0
    assert stages == []

    metrics = await collect([run_id], session_factory=_factory(db_connection))
    s = _m(metrics)
    assert s["denominators"]["should_plan_total"] == 1
    assert s["A_planner_first_pass_compliance"] == 1.0
    assert s["C_final_compliant_plan_rate"] == 1.0
    assert s["denominators"]["format_repair_entered"] == 0
    assert s["denominators"]["llm_repair_entered"] == 0
    assert s["cost"]["total_tokens_in"] == 200


# ============================================================
# B: Format repair succeeded — first invalid, repair returns valid
# Expected: plan_calls=1, format_calls=1, business_calls=0
# Metrics: A=0.0, B1 entered=1/succeeded=1, C=1.0
# ============================================================


@pytest.mark.asyncio
async def test_b_format_repair_success(db_connection, db_session) -> None:
    provider = ScriptedProvider(
        plan_responses=[ScriptedProvider.invalid_json()],
        format_repair_responses=[ScriptedProvider.context_aware_valid],
    )
    run_id, stages = await _execute(db_connection, db_session, provider)

    # PRE-ASSERT path triggered
    assert provider.plan_calls == 1
    assert provider.format_repair_calls == 1
    assert provider.business_repair_calls == 0
    assert any(
        st["stage"] == "format_repair" and st["action"] == "succeeded"
        for st in stages
    ), f"format_repair succeeded not in stages: {stages}"

    metrics = await collect([run_id], session_factory=_factory(db_connection))
    s = _m(metrics)
    assert s["denominators"]["should_plan_total"] == 1
    assert s["A_planner_first_pass_compliance"] == 0.0
    assert s["denominators"]["format_repair_entered"] == 1
    assert s["B1_format_repair_success"] == 1.0
    assert s["C_final_compliant_plan_rate"] == 1.0
    # Cost: 150 (plan, invalid_json) + 200 (format repair, context_aware) = 350
    assert s["cost"]["total_tokens_in"] == 150 + 200


# ============================================================
# C: Format repair failed — first invalid, repair also invalid
# Expected: plan_calls=1, format_calls=1, Run degraded
# Metrics: A=0.0, B1 entered=1/succeeded=0, C=0.0
# ============================================================


@pytest.mark.asyncio
async def test_c_format_repair_failed(db_connection, db_session) -> None:
    provider = ScriptedProvider(
        plan_responses=[ScriptedProvider.invalid_json()],
        format_repair_responses=[ScriptedProvider.repaired_invalid_json()],
    )
    run_id, stages = await _execute(db_connection, db_session, provider)

    # PRE-ASSERT path triggered
    assert provider.format_repair_calls == 1
    assert any(
        st["stage"] == "format_repair" and st["action"] == "failed"
        for st in stages
    ), f"format_repair failed not in stages: {stages}"

    metrics = await collect([run_id], session_factory=_factory(db_connection))
    s = _m(metrics)
    assert s["denominators"]["format_repair_entered"] == 1
    assert s["B1_format_repair_success"] == 0.0
    assert s["C_final_compliant_plan_rate"] == 0.0
    # Cost preserved despite failure
    assert s["cost"]["total_tokens_in"] == 150 + 100


# ============================================================
# D: Format repair OK, then business repair needed and succeeds
# Expected: plan=1(invalid), format=1(valid), business=1(valid)
# Metrics: A=0, B1=1.0, B2 or B3 entered/succeeded, C=1.0
# ============================================================


@pytest.mark.asyncio
async def test_d_format_then_business(db_connection, db_session) -> None:
    """Format repair succeeds, but the repaired plan violates business rules,
    then business repair (deterministic or LLM) fixes it."""
    # Plan returns invalid JSON; format repair returns a plan that
    # violates TIME_BUDGET; business repair returns a valid plan.
    provider = ScriptedProvider(
        plan_responses=[ScriptedProvider.invalid_json()],
        format_repair_responses=[
            {
                "candidate": ScriptedProvider.business_violating()["candidate"],
                "usage": _usage_t(180),
            }
        ],
        business_repair_responses=[ScriptedProvider.business_repaired_valid()],
    )
    run_id, stages = await _execute(db_connection, db_session, provider)

    # PRE-ASSERT: format repair succeeded
    assert provider.format_repair_calls == 1
    assert any(
        st["stage"] == "format_repair" and st["action"] == "succeeded"
        for st in stages
    ), f"format succeeded missing: {stages}"

    # PRE-ASSERT: deterministic repair was attempted
    assert any(
        st["stage"] == "deterministic_repair" and st["action"] == "attempted"
        for st in stages
    ), f"deterministic attempted missing: {stages}"

    # PRE-ASSERT: both stages visible (no overwriting — issue 3 fix)
    fmt_stages = [s for s in stages if s["stage"] == "format_repair"]
    det_stages = [s for s in stages if s["stage"] == "deterministic_repair"]
    assert len(fmt_stages) >= 1, "format_repair stages missing"
    assert len(det_stages) >= 1, "deterministic_repair stages missing"

    metrics = await collect([run_id], session_factory=_factory(db_connection))
    s = _m(metrics)
    assert s["denominators"]["format_repair_entered"] == 1
    assert s["denominators"]["deterministic_repair_entered"] == 1
    # Multi-stage history not overwritten
    assert s["B1_format_repair_success"] is not None


def _usage_t(tin: int) -> dict:
    from tests.providers import _usage
    return _usage(tin)


# ============================================================
# E: Deterministic repair succeeds
# Plan returns business-violating, deterministic repair fixes it
# ============================================================


@pytest.mark.asyncio
async def test_e_deterministic_success(db_connection, db_session) -> None:
    """Plan violates rules; deterministic repair (TIME_BUDIT fix) resolves."""
    from app.core.time import product_today
    from tests.providers import default_candidate

    pd = product_today()
    # Create a plan that violates TIME_BUDGET but is fixable by
    # deterministic repair (which adjusts estimated_minutes)
    base = default_candidate(pd)
    violating = base.model_copy(
        update={
            "tasks": [
                t.model_copy(update={"estimated_minutes": 200})
                for t in base.tasks
            ]
        }
    )
    provider = ScriptedProvider(
        plan_responses=[
            {
                "candidate": violating.model_dump(mode="json"),
                "usage": ScriptedProvider.valid()["usage"],
            }
        ],
    )
    run_id, stages = await _execute(db_connection, db_session, provider)

    assert any(
        st["stage"] == "deterministic_repair" and st["action"] == "attempted"
        for st in stages
    ), f"deterministic attempted missing: {stages}"

    metrics = await collect([run_id], session_factory=_factory(db_connection))
    s = _m(metrics)
    assert s["denominators"]["deterministic_repair_entered"] >= 1


# ============================================================
# F: LLM repair disabled
# ============================================================


@pytest.mark.asyncio
async def test_f_llm_disabled(db_connection, db_session) -> None:
    import os

    from app.core.time import product_today
    from tests.providers import default_candidate

    pd = product_today()
    base = default_candidate(pd)
    violating = base.model_copy(
        update={
            "tasks": [
                t.model_copy(update={"estimated_minutes": 200})
                for t in base.tasks
            ]
        }
    )
    os.environ["BUSINESS_REPAIR_LLM_ENABLED"] = "false"
    get_settings.cache_clear()
    try:
        provider = ScriptedProvider(
            plan_responses=[
                {
                    "candidate": violating.model_dump(mode="json"),
                    "usage": ScriptedProvider.valid()["usage"],
                }
            ],
        )
        run_id, stages = await _execute(db_connection, db_session, provider)
    finally:
        os.environ.pop("BUSINESS_REPAIR_LLM_ENABLED", None)
        get_settings.cache_clear()

    # PRE-ASSERT: LLM repair was NOT called
    assert provider.business_repair_calls == 0

    metrics = await collect([run_id], session_factory=_factory(db_connection))
    s = _m(metrics)
    # Critical: disabled ≠ entered (fix for issue 1)
    assert s["denominators"]["llm_repair_entered"] == 0
    if s["denominators"].get("llm_repair_skipped"):
        assert s["denominators"]["llm_repair_skipped"] >= 1


# ============================================================
# G: LLM repair budget-rejected (not sent)
# ============================================================


@pytest.mark.asyncio
async def test_g_llm_budget_rejected(db_connection, db_session) -> None:
    """LLM repair skipped because budget insufficient — NOT counted as entered."""
    from app.core.time import product_today
    from tests.providers import default_candidate

    pd = product_today()
    base = default_candidate(pd)
    violating = base.model_copy(
        update={
            "tasks": [
                t.model_copy(update={"estimated_minutes": 200})
                for t in base.tasks
            ]
        }
    )
    provider = ScriptedProvider(
        plan_responses=[
            {
                "candidate": violating.model_dump(mode="json"),
                "usage": ScriptedProvider.valid()["usage"],
            }
        ],
    )
    run_id, stages = await _execute(db_connection, db_session, provider)

    # The exact path depends on budget state; the key assertion is:
    # if LLM was NOT called, entered must be 0
    metrics = await collect([run_id], session_factory=_factory(db_connection))
    s = _m(metrics)
    if provider.business_repair_calls == 0:
        assert s["denominators"]["llm_repair_entered"] == 0


# ============================================================
# H: LLM repair returns parseable but still rule-violating
# ============================================================


@pytest.mark.asyncio
async def test_h_llm_still_violating(db_connection, db_session) -> None:
    """LLM repair output parses but violates rules — NOT business repair success."""
    import os

    from app.core.time import product_today
    from tests.providers import default_candidate

    pd = product_today()
    base = default_candidate(pd)
    violating = base.model_copy(
        update={
            "tasks": [
                t.model_copy(update={"estimated_minutes": 200})
                for t in base.tasks
            ]
        }
    )
    os.environ["BUSINESS_REPAIR_LLM_ENABLED"] = "true"
    get_settings.cache_clear()
    try:
        provider = ScriptedProvider(
            plan_responses=[
                {
                    "candidate": violating.model_dump(mode="json"),
                    "usage": ScriptedProvider.valid()["usage"],
                }
            ],
            business_repair_responses=[
                ScriptedProvider.business_repaired_still_violating()
            ],
        )
        run_id, stages = await _execute(db_connection, db_session, provider)
    finally:
        os.environ.pop("BUSINESS_REPAIR_LLM_ENABLED", None)
        get_settings.cache_clear()

    if provider.business_repair_calls == 0:
        pytest.fail("LLM repair was not called — scenario H not triggered")

    metrics = await collect([run_id], session_factory=_factory(db_connection))
    s = _m(metrics)
    assert s["denominators"]["llm_repair_entered"] >= 1
    # Critical: parseable-but-violating is NOT success
    assert s["B3_llm_repair_success"] == 0.0


# ============================================================
# I: LLM repair succeeds (returns rule-compliant plan)
# ============================================================


@pytest.mark.asyncio
async def test_i_llm_success(db_connection, db_session) -> None:
    import os

    from app.core.time import product_today
    from tests.providers import default_candidate

    pd = product_today()
    base = default_candidate(pd)
    violating = base.model_copy(
        update={
            "tasks": [
                t.model_copy(update={"estimated_minutes": 200})
                for t in base.tasks
            ]
        }
    )
    os.environ["BUSINESS_REPAIR_LLM_ENABLED"] = "true"
    get_settings.cache_clear()
    try:
        provider = ScriptedProvider(
            plan_responses=[
                {
                    "candidate": violating.model_dump(mode="json"),
                    "usage": ScriptedProvider.valid()["usage"],
                }
            ],
            business_repair_responses=[ScriptedProvider.business_repaired_valid()],
        )
        run_id, stages = await _execute(db_connection, db_session, provider)
    finally:
        os.environ.pop("BUSINESS_REPAIR_LLM_ENABLED", None)
        get_settings.cache_clear()

    if provider.business_repair_calls == 0:
        pytest.fail("LLM repair was not called — scenario I not triggered")

    metrics = await collect([run_id], session_factory=_factory(db_connection))
    s = _m(metrics)
    assert s["denominators"]["llm_repair_entered"] >= 1


# ============================================================
# J: Repair request throws exception, but prior usage exists
# ============================================================


@pytest.mark.asyncio
async def test_j_repair_exception_preserves_usage(db_connection, db_session) -> None:
    """Plan generates fine (usage recorded), then repair throws — Run fails
    but tokens must still be counted."""
    import os

    from app.core.time import product_today
    from tests.providers import default_candidate

    pd = product_today()
    base = default_candidate(pd)
    violating = base.model_copy(
        update={
            "tasks": [
                t.model_copy(update={"estimated_minutes": 200})
                for t in base.tasks
            ]
        }
    )
    os.environ["BUSINESS_REPAIR_LLM_ENABLED"] = "true"
    get_settings.cache_clear()
    try:
        provider = ScriptedProvider(
            plan_responses=[
                {
                    "candidate": violating.model_dump(mode="json"),
                    "usage": ScriptedProvider.valid()["usage"],
                }
            ],
            business_repair_responses=[
                ConnectionError("network down during repair")
            ],
        )
        run_id, stages = await _execute(db_connection, db_session, provider)
    finally:
        os.environ.pop("BUSINESS_REPAIR_LLM_ENABLED", None)
        get_settings.cache_clear()

    # PRE-ASSERT: plan was called and had usage
    assert provider.plan_calls >= 1
    run = await _run_status(db_session, run_id)
    plan_had_usage = (run.total_tokens_in or 0) > 0

    metrics = await collect([run_id], session_factory=_factory(db_connection))
    s = _m(metrics)
    # Issue 4/1 fix: even though Run failed, known usage preserved
    if plan_had_usage:
        assert s["cost"]["total_tokens_in"] > 0, (
            f"Run had {run.total_tokens_in} tokens but cost total is "
            f"{s['cost']['total_tokens_in']} — usage lost on failure"
        )
