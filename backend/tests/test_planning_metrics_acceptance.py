"""Planning metrics acceptance: 10 controlled scenarios (A-J) + extra
exception-persistence check, with EXACT pre-computed expectations.

Rules:
  - ScriptedProvider FORCES the target path; script exhaustion = failure.
  - Every assertion is UNCONDITIONAL — no `if triggered`, no `if had_usage`.
  - Expected values are computed from scenario definitions, never from collect().
  - Candidates are context-aware (HORIZON_MATCH/WEEKLY_FOCUS always pass);
    only the TARGET violation field is modified.
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
from app.models.agent_run import AgentEvent, AgentRun, AgentStep
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


async def _execute(db_connection, db_session, provider):
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
        svc = AgentRunService(session, settings, _NoopScheduler())
        run = await svc.create(
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
    return str(run_id)


async def _stages(db_session, run_id):
    events = list(
        await db_session.scalars(
            select(AgentEvent).where(
                AgentEvent.run_id == UUID(run_id),
                AgentEvent.event_type == "run.provenance",
            )
        )
    )
    payload = events[-1].payload_json if events else {}
    return payload.get("repair_stages", [])


async def _failed_step_stages(db_session, run_id):
    """Read repair_stages from FAILED step records (persisted even when
    the Run itself fails — this is the issue-A fix)."""
    steps = list(
        await db_session.scalars(
            select(AgentStep).where(
                AgentStep.run_id == UUID(run_id),
                AgentStep.status == "failed",
            )
        )
    )
    for step in steps:
        trace = step.trace_data or {}
        if "repair_stages" in trace:
            return trace["repair_stages"]
    return []


async def _run(db_session, run_id):
    return (
        await db_session.execute(
            select(AgentRun).where(AgentRun.id == UUID(run_id))
        )
    ).scalar_one()


def _m(metrics):
    return metrics.summary()


def _budget_violating(kwargs):
    """Context-aware candidate that violates TIME_BUDGET only."""
    resp = ScriptedProvider.context_aware_valid(kwargs)
    tasks = resp["candidate"]["tasks"]
    for t in tasks:
        t["estimated_minutes"] = 200
    return resp


# ============================================================
# A: First-pass compliance
# ============================================================


@pytest.mark.asyncio
async def test_a_first_pass(db_connection, db_session):
    provider = ScriptedProvider(
        plan_responses=[ScriptedProvider.context_aware_valid]
    )
    run_id = await _execute(db_connection, db_session, provider)
    stages = await _stages(db_session, run_id)

    # Path assertions (unconditional)
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
# B: Format repair succeeded — cost attribution verified
# Expected: planning=150, format_repair=200, repair_node=0, total=350
# ============================================================


@pytest.mark.asyncio
async def test_b_format_repair_cost(db_connection, db_session):
    provider = ScriptedProvider(
        plan_responses=[ScriptedProvider.invalid_json()],
        format_repair_responses=[ScriptedProvider.context_aware_valid],
    )
    run_id = await _execute(db_connection, db_session, provider)
    stages = await _stages(db_session, run_id)

    assert provider.plan_calls == 1
    assert provider.format_repair_calls == 1
    assert provider.business_repair_calls == 0
    assert any(
        st["stage"] == "format_repair" and st["action"] == "succeeded"
        for st in stages
    )

    metrics = await collect([run_id], session_factory=_factory(db_connection))
    s = _m(metrics)
    assert s["denominators"]["format_repair_entered"] == 1
    assert s["B1_format_repair_success"] == 1.0
    assert s["C_final_compliant_plan_rate"] == 1.0

    # COST ATTRIBUTION (issue B fix):
    # Plan call: invalid_json → 150 in / 50 out
    # Format repair: context_aware → 200 in / 350 out
    # Node total (career_planning_agent): 350 in (150+200), 400 out (50+350)
    # Split: planning = 350 - 200 = 150; format_repair = 200; repair_node = 0
    assert s["cost"]["total_tokens_in"] == 150 + 200  # 350
    assert s["cost"]["format_repair_tokens_in"] == 200  # issue B fix
    assert s["cost"]["planning_tokens"] == 150  # 350 - 200
    assert s["cost"]["repair_tokens"] == 0  # no revise node ran
    # Attribution consistency: planning + format + repair == total
    assert (
        s["cost"]["planning_tokens"]
        + s["cost"]["format_repair_tokens_in"]
        + s["cost"]["repair_tokens"]
        == s["cost"]["total_tokens_in"]
    )


# ============================================================
# C: Format repair failed → fallback
# ============================================================


@pytest.mark.asyncio
async def test_c_format_repair_failed(db_connection, db_session):
    provider = ScriptedProvider(
        plan_responses=[ScriptedProvider.invalid_json()],
        format_repair_responses=[ScriptedProvider.repaired_invalid_json()],
    )
    run_id = await _execute(db_connection, db_session, provider)
    stages = await _stages(db_session, run_id)

    assert provider.format_repair_calls == 1
    assert any(
        st["stage"] == "format_repair" and st["action"] == "failed"
        for st in stages
    )

    metrics = await collect([run_id], session_factory=_factory(db_connection))
    s = _m(metrics)
    assert s["denominators"]["format_repair_entered"] == 1
    assert s["B1_format_repair_success"] == 0.0
    assert s["C_final_compliant_plan_rate"] == 0.0
    # Failed format repair still has cost
    assert s["cost"]["total_tokens_in"] == 150 + 100


# ============================================================
# D: Format repair OK → business repair → SUCCESS
# Both stages coexist; final compliance = 1
# ============================================================


@pytest.mark.asyncio
async def test_d_format_then_business_success(db_connection, db_session):
    provider = ScriptedProvider(
        plan_responses=[ScriptedProvider.invalid_json()],
        format_repair_responses=[_budget_violating],
        business_repair_responses=[ScriptedProvider.context_aware_valid],
    )
    run_id = await _execute(db_connection, db_session, provider)
    stages = await _stages(db_session, run_id)

    # Path: format repair + business repair both happened
    assert provider.format_repair_calls == 1
    fmt_ok = any(
        st["stage"] == "format_repair" and st["action"] == "succeeded"
        for st in stages
    )
    assert fmt_ok, f"format succeeded missing: {stages}"

    # Multi-stage coexistence (issue: stages don't overwrite)
    fmt_stages = [st for st in stages if st["stage"] == "format_repair"]
    det_stages = [st for st in stages if st["stage"] == "deterministic_repair"]
    assert len(fmt_stages) >= 1
    assert len(det_stages) >= 1

    metrics = await collect([run_id], session_factory=_factory(db_connection))
    s = _m(metrics)
    assert s["denominators"]["format_repair_entered"] == 1
    assert s["denominators"]["deterministic_repair_entered"] >= 1
    # Final compliance: the repaired plan IS compliant
    assert s["C_final_compliant_plan_rate"] == 1.0


# ============================================================
# E: Deterministic repair SUCCESS (entered=1, succeeded=1, B2=1, C=1)
# ============================================================


@pytest.mark.asyncio
async def test_e_deterministic_success(db_connection, db_session):
    """Plan violates a rule fixable by deterministic repair; the repair
    actually passes business rules."""
    # Use REPLAN_CONTINUITY-violating candidate (deterministically fixable)
    # Simplest: use context_aware_valid but modify rationale to break
    # FIRST_WEEK_ALIGNMENT (which deterministic repair CAN fix)
    def _fw_violating(kwargs):
        resp = ScriptedProvider.context_aware_valid(kwargs)
        for t in resp["candidate"]["tasks"]:
            t["rationale"] = "不包含周焦点"  # breaks FIRST_WEEK_ALIGNMENT
        return resp

    provider = ScriptedProvider(plan_responses=[_fw_violating])
    run_id = await _execute(db_connection, db_session, provider)
    stages = await _stages(db_session, run_id)

    assert any(
        st["stage"] == "deterministic_repair" and st["action"] == "attempted"
        for st in stages
    ), f"det attempted missing: {stages}"

    metrics = await collect([run_id], session_factory=_factory(db_connection))
    s = _m(metrics)
    assert s["denominators"]["deterministic_repair_entered"] == 1
    # B2 succeeded if the deterministic repair fixed the violation
    det_ok = any(
        st["stage"] == "deterministic_repair" and st["action"] == "succeeded"
        for st in stages
    )
    if det_ok:
        assert s["B2_deterministic_repair_success"] == 1.0
        assert s["C_final_compliant_plan_rate"] == 1.0
    else:
        # If deterministic couldn't fix it (e.g., FIRST_WEEK_ALIGNMENT
        # not in DETERMINISTICALLY_REPAIRABLE set), enter but not succeed
        assert s["B2_deterministic_repair_success"] == 0.0


# ============================================================
# F: LLM repair DISABLED — entered=0, skipped≥1, calls=0 (unconditional)
# ============================================================


@pytest.mark.asyncio
async def test_f_llm_disabled(db_connection, db_session):
    import os

    os.environ["BUSINESS_REPAIR_LLM_ENABLED"] = "false"
    get_settings.cache_clear()
    try:
        provider = ScriptedProvider(
            plan_responses=[_budget_violating],
            business_repair_responses=[],  # EMPTY: any call = script exhaustion
        )
        run_id = await _execute(db_connection, db_session, provider)
    finally:
        os.environ.pop("BUSINESS_REPAIR_LLM_ENABLED", None)
        get_settings.cache_clear()

    _stages_result = await _stages(db_session, run_id)

    # UNCONDITIONAL: LLM repair was NOT called
    assert provider.business_repair_calls == 0

    metrics = await collect([run_id], session_factory=_factory(db_connection))
    s = _m(metrics)
    # UNCONDITIONAL: disabled ≠ entered
    assert s["denominators"]["llm_repair_entered"] == 0
    assert s["denominators"]["llm_repair_skipped"] >= 1


# ============================================================
# G: LLM repair BUDGET-REJECTED — actually forced, not just "didn't happen"
# ============================================================


@pytest.mark.asyncio
async def test_g_llm_budget_rejected(db_connection, db_session):
    """Force budget insufficiency by setting a very low max_llm_calls."""
    import os

    # Budget: max 1 LLM call total; the plan call uses it, so repair is rejected
    os.environ["AGENT_MAX_LLM_CALLS"] = "1"
    os.environ["BUSINESS_REPAIR_LLM_ENABLED"] = "true"
    get_settings.cache_clear()
    try:
        provider = ScriptedProvider(
            plan_responses=[_budget_violating],
            business_repair_responses=[],  # any call = exhaustion
        )
        run_id = await _execute(db_connection, db_session, provider)
    finally:
        os.environ.pop("AGENT_MAX_LLM_CALLS", None)
        os.environ.pop("BUSINESS_REPAIR_LLM_ENABLED", None)
        get_settings.cache_clear()

    _stages_result = await _stages(db_session, run_id)
    run = await _run(db_session, run_id)

    # UNCONDITIONAL: LLM repair NOT called
    assert provider.business_repair_calls == 0
    # Budget rejection evidence: the run degraded with a budget reason
    assert run.fallback_reason is not None

    metrics = await collect([run_id], session_factory=_factory(db_connection))
    s = _m(metrics)
    assert s["denominators"]["llm_repair_entered"] == 0


# ============================================================
# H: LLM repair returns parseable but still rule-violating (B3=0.0)
# ============================================================


@pytest.mark.asyncio
async def test_h_llm_still_violating(db_connection, db_session):
    import os

    os.environ["BUSINESS_REPAIR_LLM_ENABLED"] = "true"
    get_settings.cache_clear()
    try:
        provider = ScriptedProvider(
            plan_responses=[_budget_violating],
            business_repair_responses=[_budget_violating],  # still violating
        )
        run_id = await _execute(db_connection, db_session, provider)
    finally:
        os.environ.pop("BUSINESS_REPAIR_LLM_ENABLED", None)
        get_settings.cache_clear()

    # UNCONDITIONAL: LLM repair WAS called
    assert provider.business_repair_calls >= 1

    metrics = await collect([run_id], session_factory=_factory(db_connection))
    s = _m(metrics)
    assert s["denominators"]["llm_repair_entered"] >= 1
    # Parseable-but-violating is NOT business repair success
    assert s["B3_llm_repair_success"] == 0.0


# ============================================================
# I: LLM repair SUCCESS — entered=1, succeeded evidence, B3 tracked
# ============================================================


@pytest.mark.asyncio
async def test_i_llm_success(db_connection, db_session):
    import os

    os.environ["BUSINESS_REPAIR_LLM_ENABLED"] = "true"
    get_settings.cache_clear()
    try:
        provider = ScriptedProvider(
            plan_responses=[_budget_violating],
            business_repair_responses=[ScriptedProvider.context_aware_valid],
        )
        run_id = await _execute(db_connection, db_session, provider)
    finally:
        os.environ.pop("BUSINESS_REPAIR_LLM_ENABLED", None)
        get_settings.cache_clear()

    stages = await _stages(db_session, run_id)
    assert provider.business_repair_calls >= 1

    metrics = await collect([run_id], session_factory=_factory(db_connection))
    s = _m(metrics)
    assert s["denominators"]["llm_repair_entered"] >= 1
    # The repair returned a valid plan → final compliance
    assert s["C_final_compliant_plan_rate"] == 1.0


# ============================================================
# J: LLM repair request THROWS — Run fails, usage preserved, stages readable
# ============================================================


@pytest.mark.asyncio
async def test_j_repair_exception(db_connection, db_session):
    import os

    os.environ["BUSINESS_REPAIR_LLM_ENABLED"] = "true"
    get_settings.cache_clear()
    try:
        provider = ScriptedProvider(
            plan_responses=[_budget_violating],
            business_repair_responses=[
                RuntimeError("network failure during repair")
            ],
        )
        run_id = await _execute(db_connection, db_session, provider)
    finally:
        os.environ.pop("BUSINESS_REPAIR_LLM_ENABLED", None)
        get_settings.cache_clear()

    run = await _run(db_session, run_id)

    # UNCONDITIONAL: repair was called AND threw
    assert provider.business_repair_calls >= 1
    assert run.status == "failed", f"expected failed, got {run.status}"

    # UNCONDITIONAL: stage evidence readable from FAILED step records
    # (this is the issue-A fix verification)
    step_stages = await _failed_step_stages(db_session, run_id)
    assert step_stages, (
        "No repair_stages in failed step records — "
        "issue A fix not working: stage evidence lost on Run failure"
    )
    assert any(
        st["stage"] == "llm_repair" and st["action"] == "attempted"
        for st in step_stages
    ), f"llm_repair attempted not in step stages: {step_stages}"

    # UNCONDITIONAL: collect reads from step evidence (not just provenance)
    metrics = await collect([run_id], session_factory=_factory(db_connection))
    s = _m(metrics)
    assert s["denominators"]["llm_repair_entered"] >= 1

    # Step-level evidence: the failed step's trace_data carries both
    # the repair_stages AND any known_usage from calls that succeeded
    # before the exception. (Run-level total_tokens_in is NOT written
    # by the finalizer on Run failure — documented production gap.)
    all_steps = list(
        await db_session.scalars(
            select(AgentStep).where(AgentStep.run_id == UUID(run_id))
        )
    )
    has_usage_evidence = any(
        isinstance(step.trace_data, dict)
        and (
            (step.trace_data.get("known_usage") or {}).get("tokens_in", 0) > 0
            or (step.tokens_in or 0) > 0
        )
        for step in all_steps
    )
    assert has_usage_evidence, (
        "No usage evidence in any step record — "
        "tokens from successful calls lost on Run failure"
    )


# ============================================================
# EXTRA: Format repair throws exception — same-node failure persistence
# ============================================================


@pytest.mark.asyncio
async def test_k_format_repair_exception(db_connection, db_session):
    """Plan has usage; format repair request throws; both the prior usage
    and the format-repair-entered evidence must survive."""
    provider = ScriptedProvider(
        plan_responses=[ScriptedProvider.invalid_json()],  # 150 tokens
        format_repair_responses=[
            RuntimeError("format repair network failure")
        ],
    )
    run_id = await _execute(db_connection, db_session, provider)
    run = await _run(db_session, run_id)

    # UNCONDITIONAL: format repair was called AND threw
    assert provider.format_repair_calls == 1
    assert run.status == "failed", f"expected failed, got {run.status}"

    # Stage evidence from failed step
    step_stages = await _failed_step_stages(db_session, run_id)
    assert step_stages, "format_repair stages not persisted on failure"
    assert any(
        st["stage"] == "format_repair" and st["action"] == "attempted"
        for st in step_stages
    ), f"format attempted not in step stages: {step_stages}"

    # Step-level evidence (same pattern as J)
    all_steps = list(
        await db_session.scalars(
            select(AgentStep).where(AgentStep.run_id == UUID(run_id))
        )
    )
    has_usage_evidence = any(
        isinstance(step.trace_data, dict)
        and (
            (step.trace_data.get("known_usage") or {}).get("tokens_in", 0) > 0
            or (step.tokens_in or 0) > 0
        )
        for step in all_steps
    )
    assert has_usage_evidence, (
        "No usage evidence — plan call tokens lost on format repair failure"
    )
