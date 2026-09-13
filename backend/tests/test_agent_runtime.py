"""PostgreSQL integration tests for the complete Stage 2 Mock Runtime."""

from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
)

from app.agent.errors import AgentLeaseLostError, RunCancelledError
from app.agent.executor import AgentRunExecutor
from app.agent.finalizer import AgentRunFinalizer
from app.agent.node_runner import NodeOutput, NodeRunner
from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.core.security import TokenService
from app.core.time import product_today
from app.harness.budget import BudgetGuard, CancellationToken
from app.models.agent_run import AgentEvent, AgentRun, AgentStep
from app.models.plan import Plan, Task
from app.providers.llm import MockPlanningProvider
from app.schemas.agent_runs import AgentRunCancelRequest
from app.schemas.enums import CareerStage, GoalType, SkillLevel
from app.schemas.profile import ProfilePatchRequest, ProfilePutRequest
from app.services.agent_runs import AgentRunService
from app.services.auth import AuthService
from app.services.plans import PlanQueryService
from app.services.profiles import ProfileService


class ManualExecutor(AgentRunExecutor):
    def __init__(self) -> None:
        self.submitted: list[UUID] = []

    def submit(self, run_id: UUID) -> None:
        self.submitted.append(run_id)

    async def request_cancel(self, run_id: UUID) -> None:
        del run_id


def runtime_factory(
    connection: AsyncConnection,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=connection,
        class_=AsyncSession,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )


async def create_user(session: AsyncSession, *, with_profile: bool = True) -> UUID:
    user = (await AuthService(session, TokenService(get_settings())).login_guest(None)).user
    if with_profile:
        await ProfileService(session).put(
            user_id=user.id,
            payload=ProfilePutRequest(
                goal_type=GoalType.AGENT_APP,
                stage=CareerStage.PREPARING,
                time_budget_minutes=90,
                skill_level=SkillLevel.INTERMEDIATE,
                skill_summary="FastAPI and PostgreSQL",
                start_date=product_today(),
                deadline=product_today() + timedelta(days=34),
            ),
            idempotency_key=f"profile-{user.id}",
        )
    return user.id


async def create_run(
    session: AsyncSession,
    user_id: UUID,
    *,
    message: str = "帮我制定未来五周的求职计划",
    key: str = "run-1",
    settings: Settings | None = None,
    scheduler: AgentRunExecutor | None = None,
) -> AgentRun:
    service = AgentRunService(
        session,
        settings or get_settings(),
        scheduler or ManualExecutor(),
    )
    return await service.create(
        user_id=user_id,
        message=message,
        hint_intent="create_plan",
        goal_type_override=None,
        source_plan_id=None,
        idempotency_key=key,
    )


async def refresh_run(session: AsyncSession, run_id: UUID) -> AgentRun:
    session.expire_all()
    run = await session.get(AgentRun, run_id)
    assert run is not None
    await session.refresh(run)
    return run


@pytest.mark.asyncio
async def test_happy_run_persists_plan_tasks_snapshot_and_last_terminal_event(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    user_id = await create_user(db_session)
    run = await create_run(db_session, user_id)
    await AgentRunExecutor(runtime_factory(db_connection)).execute(run.id)
    run = await refresh_run(db_session, run.id)

    events = list(
        await db_session.scalars(
            select(AgentEvent).where(AgentEvent.run_id == run.id).order_by(AgentEvent.sequence)
        )
    )
    plan = await db_session.scalar(select(Plan).where(Plan.source_run_id == run.id))
    tasks = list(await db_session.scalars(select(Task).where(Task.user_id == user_id)))
    intent_step = await db_session.scalar(
        select(AgentStep).where(
            AgentStep.run_id == run.id,
            AgentStep.node_name == "intent_router",
        )
    )

    assert run.status == "completed"
    assert run.result_kind == "plan"
    assert run.final_plan_id is not None
    assert run.input_snapshot_json is not None
    assert intent_step is not None
    assert intent_step.trace_data["router_version"] == "intent-rule-v3"
    assert intent_step.trace_data["intent"] == "create_plan"
    assert intent_step.trace_data["confidence_band"] == "high"
    assert intent_step.trace_data["matched_rule_ids"]
    assert plan is not None
    assert len(plan.weekly_focus_json) == 5
    assert len(tasks) == 7
    assert [task.scheduled_date for task in tasks] == [
        plan.plan_date + timedelta(days=offset) for offset in range(7)
    ]
    assert all(task.estimated_minutes <= 90 for task in tasks)
    assert all(task.starter_action.startswith("1. ") for task in tasks)
    assert all(task.rationale is not None for task in tasks)
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    started_nodes = [
        event.payload_json["node_name"] for event in events if event.event_type == "node.started"
    ]
    # The two context loaders run in the same LangGraph superstep; their
    # start-event order is scheduler-dependent, so compare as a set.
    assert started_nodes[:2] == ["risk_gate", "intent_router"]
    assert set(started_nodes[2:4]) == {"memory_loader", "evidence_loader"}
    assert started_nodes[4:] == [
        "context_builder",
        "career_planning_agent",
        "rule_validator",
        "companion_response",
        "persist",
    ]
    assert events[-1].event_type == "run.completed"
    assert sum(event.event_type.startswith("run.") for event in events[-1:]) == 1
    assert (
        sum(
            event.event_type in {"run.completed", "run.degraded", "run.failed", "run.cancelled"}
            for event in events
        )
        == 1
    )
    input_snapshot = run.input_snapshot_json
    config_snapshot = run.config_snapshot_json
    await ProfileService(db_session).patch(
        user_id=user_id,
        payload=ProfilePatchRequest(version=1, time_budget_minutes=30),
    )
    run = await refresh_run(db_session, run.id)
    assert run.input_snapshot_json == input_snapshot
    assert run.config_snapshot_json == config_snapshot


@pytest.mark.asyncio
async def test_database_lease_allows_only_one_worker_to_claim_a_run(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    user_id = await create_user(db_session)
    run = await create_run(db_session, user_id, key="lease-exclusive")
    first = AgentRunExecutor(runtime_factory(db_connection))
    second = AgentRunExecutor(runtime_factory(db_connection))

    claimed = await first._claim_by_id(run.id)  # noqa: SLF001
    assert claimed is not None
    assert await second._claim_by_id(run.id) is None  # noqa: SLF001

    running = await refresh_run(db_session, run.id)
    assert running.status == "running"
    assert running.worker_id is not None
    assert running.heartbeat_at is not None
    assert running.lease_expires_at is not None
    assert running.attempt_count == 1

    claimed_run, config = claimed
    await first._execute_claimed(claimed_run, config)  # noqa: SLF001
    completed = await refresh_run(db_session, run.id)
    assert completed.status == "completed"
    assert completed.worker_id is None
    assert completed.heartbeat_at is None
    assert completed.lease_expires_at is None


@pytest.mark.asyncio
async def test_query_only_run_returns_navigation_without_generating_a_plan(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    user_id = await create_user(db_session)
    run = await create_run(
        db_session,
        user_id,
        message="查看我的计划",
        key="navigate-current-plan",
    )

    await AgentRunExecutor(runtime_factory(db_connection)).execute(run.id)

    completed = await refresh_run(db_session, run.id)
    response = AgentRunService.to_response(completed)
    assert completed.status == "degraded"
    assert completed.resolved_intent == "navigate"
    assert completed.result_kind == "navigation"
    assert response.user_status == "action_required"
    assert response.result is not None
    assert response.result.model_dump(mode="json")["target_route"] == "/journey"
    assert await db_session.scalar(
        select(func.count()).select_from(Plan).where(Plan.source_run_id == run.id)
    ) == 0


@pytest.mark.asyncio
async def test_stale_attempt_cannot_terminalize_a_reassigned_run(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    user_id = await create_user(db_session)
    run = await create_run(db_session, user_id, key="lease-fencing")
    executor = AgentRunExecutor(runtime_factory(db_connection))
    claimed = await executor._claim_by_id(run.id)  # noqa: SLF001
    assert claimed is not None
    stale_run, _ = claimed

    current = await refresh_run(db_session, run.id)
    current.worker_id = "replacement-worker"
    current.attempt_count = stale_run.attempt_count + 1
    current.lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
    await db_session.flush()

    stale_finalizer = AgentRunFinalizer(
        runtime_factory(db_connection),
        None,
        worker_id=stale_run.worker_id,
        attempt_count=stale_run.attempt_count,
    )
    with pytest.raises(AgentLeaseLostError):
        await stale_finalizer.finalize_failed(
            run.id,
            error_code="AGENT_EXECUTION_FAILED",
        )

    current = await refresh_run(db_session, run.id)
    assert current.status == "running"
    assert current.worker_id == "replacement-worker"
    assert current.error_code is None


@pytest.mark.asyncio
async def test_run_latency_never_becomes_negative_when_database_clock_is_ahead(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    user_id = await create_user(db_session)
    run = await create_run(db_session, user_id, key="clock-skew")
    run.created_at = datetime.now(UTC) + timedelta(seconds=1)
    await db_session.flush()

    await AgentRunExecutor(runtime_factory(db_connection)).execute(run.id)
    run = await refresh_run(db_session, run.id)

    assert run.status == "completed"
    assert run.total_latency_ms == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "with_profile", "kind", "reason"),
    [
        ("帮我制定计划", False, "clarification", "profile_incomplete"),
        ("你好", True, "clarification", "intent_uncertain"),
        ("今天有什么任务", True, "navigation", "resource_navigation"),
        ("我想自杀", True, "safe_response", "high_risk_routed"),
    ],
)
async def test_terminal_branches_are_recoverable_without_plan(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
    message: str,
    with_profile: bool,
    kind: str,
    reason: str,
) -> None:
    user_id = await create_user(db_session, with_profile=with_profile)
    run = await create_run(db_session, user_id, message=message)
    await AgentRunExecutor(runtime_factory(db_connection)).execute(run.id)
    run = await refresh_run(db_session, run.id)

    assert run.status == "degraded"
    assert run.result_kind == kind
    assert run.fallback_reason == reason
    assert run.result_payload_json
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Plan).where(Plan.source_run_id == run.id)
        )
        == 0
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("marker", "expected_status", "expected_revise_steps"),
    [
        ("[mock:invalid-schema]", "completed", 0),
        ("[mock:invalid-schema-twice]", "degraded", 0),
        ("[mock:rule-repair]", "completed", 1),
        ("[mock:rule-fallback]", "degraded", 2),
    ],
)
async def test_repairs_are_bounded_and_fallback_is_deterministic(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
    marker: str,
    expected_status: str,
    expected_revise_steps: int,
) -> None:
    user_id = await create_user(db_session)
    run = await create_run(
        db_session,
        user_id,
        message=f"帮我制定未来五周计划 {marker}",
    )
    provider = MockPlanningProvider()
    await AgentRunExecutor(runtime_factory(db_connection), provider).execute(run.id)
    run = await refresh_run(db_session, run.id)
    revise_count = await db_session.scalar(
        select(func.count())
        .select_from(AgentStep)
        .where(
            AgentStep.run_id == run.id,
            AgentStep.node_name == "revise_or_fallback",
        )
    )

    assert run.status == expected_status
    assert revise_count == expected_revise_steps
    assert run.result_kind == "plan"
    assert provider.plan_calls == 1
    assert provider.format_repair_calls == (1 if marker.startswith("[mock:invalid-schema") else 0)
    assert provider.business_repair_calls == (1 if marker.startswith("[mock:rule-") else 0)
    if marker == "[mock:rule-fallback]":
        validator_steps = list(
            await db_session.scalars(
                select(AgentStep)
                .where(
                    AgentStep.run_id == run.id,
                    AgentStep.node_name == "rule_validator",
                )
                .order_by(AgentStep.sequence)
            )
        )
        assert validator_steps[0].trace_data["failed_checks"] == [
            "TIME_BUDGET",
            "SCHEDULE_DATE",
        ]
        assert validator_steps[1].trace_data["failed_checks"] == [
            "TIME_BUDGET",
            "SCHEDULE_DATE",
        ]
        assert validator_steps[-1].trace_data["failed_checks"] == []


@pytest.mark.asyncio
async def test_business_repair_skips_provider_when_total_budget_is_insufficient(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    user_id = await create_user(db_session)
    run = await create_run(
        db_session,
        user_id,
        message="帮我制定未来五周计划 [mock:rule-repair]",
        key="repair-budget-fallback",
    )
    run.config_snapshot_json = {
        **run.config_snapshot_json,
        "max_total_tokens": 1_000,
        "max_input_tokens_per_call": 600,
        "max_output_tokens_per_call": 500,
    }
    await db_session.flush()
    provider = MockPlanningProvider()

    await AgentRunExecutor(runtime_factory(db_connection), provider).execute(run.id)
    run = await refresh_run(db_session, run.id)

    assert run.status == "degraded", (run.error_code, run.fallback_reason)
    assert run.result_kind == "plan"
    assert run.fallback_reason == "business_repair_budget_insufficient"
    assert provider.plan_calls == 1
    assert provider.business_repair_calls == 0
    terminal_count = await db_session.scalar(
        select(func.count())
        .select_from(AgentEvent)
        .where(
            AgentEvent.run_id == run.id,
            AgentEvent.event_type.in_(
                ("run.completed", "run.degraded", "run.failed", "run.cancelled")
            ),
        )
    )
    assert terminal_count == 1


@pytest.mark.asyncio
async def test_timeout_and_persist_failure_converge_without_plan(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    user_id = await create_user(db_session)
    timeout_settings = get_settings().model_copy(update={"agent_deadline_seconds": 1})
    timeout_run = await create_run(
        db_session,
        user_id,
        message="制定计划 [mock:timeout]",
        key="timeout",
        settings=timeout_settings,
    )
    await AgentRunExecutor(runtime_factory(db_connection)).execute(timeout_run.id)
    timeout_run = await refresh_run(db_session, timeout_run.id)
    assert timeout_run.status == "failed"
    assert timeout_run.error_code == "AGENT_DEADLINE_EXCEEDED"
    timeout_events = list(
        await db_session.scalars(
            select(AgentEvent)
            .where(AgentEvent.run_id == timeout_run.id)
            .order_by(AgentEvent.sequence)
        )
    )
    assert timeout_events[-1].event_type == "run.failed"

    failure_run = await create_run(
        db_session,
        user_id,
        message="制定计划 [mock:persist-failure]",
        key="persist-failure",
    )
    await AgentRunExecutor(runtime_factory(db_connection)).execute(failure_run.id)
    failure_run = await refresh_run(db_session, failure_run.id)
    assert failure_run.status == "failed"
    assert failure_run.error_code == "PERSIST_TRANSACTION_FAILED"
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Plan).where(Plan.source_run_id == failure_run.id)
        )
        == 0
    )
    failure_events = list(
        await db_session.scalars(
            select(AgentEvent)
            .where(AgentEvent.run_id == failure_run.id)
            .order_by(AgentEvent.sequence)
        )
    )
    assert failure_events[-1].event_type == "run.failed"


@pytest.mark.asyncio
async def test_user_cancel_stops_graph_and_writes_one_terminal(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    user_id = await create_user(db_session)
    settings = get_settings().model_copy(update={"agent_deadline_seconds": 5})
    factory = runtime_factory(db_connection)
    scheduler = ManualExecutor()
    run = await create_run(
        db_session,
        user_id,
        message="制定计划 [mock:timeout]",
        key="cancel",
        settings=settings,
        scheduler=scheduler,
    )
    service = AgentRunService(db_session, settings, scheduler)
    await service.cancel(
        run_id=run.id,
        user_id=user_id,
        payload=AgentRunCancelRequest(),
        idempotency_key="cancel-1",
    )
    await AgentRunExecutor(factory).execute(run.id)
    run = await refresh_run(db_session, run.id)
    terminal_count = await db_session.scalar(
        select(func.count())
        .select_from(AgentEvent)
        .where(
            AgentEvent.run_id == run.id,
            AgentEvent.event_type.in_(
                ("run.completed", "run.degraded", "run.failed", "run.cancelled")
            ),
        )
    )
    assert run.status == "cancelled"
    assert run.error_code == "RUN_CANCELLED"
    assert terminal_count == 1


@pytest.mark.asyncio
async def test_cancel_from_another_worker_is_observed_before_the_next_node(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    user_id = await create_user(db_session)
    run = await create_run(db_session, user_id, key="cross-worker-cancel")
    owner = AgentRunExecutor(runtime_factory(db_connection))
    claimed = await owner._claim_by_id(run.id)  # noqa: SLF001
    assert claimed is not None

    remote_scheduler = ManualExecutor()
    await AgentRunService(db_session, get_settings(), remote_scheduler).cancel(
        run_id=run.id,
        user_id=user_id,
        payload=AgentRunCancelRequest(),
        idempotency_key="cross-worker-cancel-key",
    )
    claimed_run, config = claimed
    await owner._execute_claimed(claimed_run, config)  # noqa: SLF001

    cancelled = await refresh_run(db_session, run.id)
    assert cancelled.status == "cancelled"
    assert cancelled.error_code == "RUN_CANCELLED"
    assert AgentRunService.to_response(cancelled).user_status == "cancelled"


@pytest.mark.asyncio
async def test_cross_worker_cancel_closes_the_active_step_before_terminalizing(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    user_id = await create_user(db_session)
    run = await create_run(db_session, user_id, key="cross-worker-active-step")
    owner = AgentRunExecutor(runtime_factory(db_connection))
    claimed = await owner._claim_by_id(run.id)  # noqa: SLF001
    assert claimed is not None
    claimed_run, config = claimed
    assert config is not None

    runner = NodeRunner(
        runtime_factory(db_connection),
        BudgetGuard(config, claimed_run.deadline_at, CancellationToken()),
        config.node_timeouts_seconds,
        worker_id=claimed_run.worker_id,
        attempt_count=claimed_run.attempt_count,
    )

    async def cancel_during_node() -> NodeOutput[str]:
        await AgentRunService(db_session, get_settings(), ManualExecutor()).cancel(
            run_id=run.id,
            user_id=user_id,
            payload=AgentRunCancelRequest(),
            idempotency_key="cross-worker-active-step-key",
        )
        return NodeOutput("ignored")

    with pytest.raises(RunCancelledError):
        await runner.run(run.id, "risk_gate", cancel_during_node)

    steps = list(
        await db_session.scalars(select(AgentStep).where(AgentStep.run_id == run.id))
    )
    assert len(steps) == 1
    assert steps[0].status == "failed"
    assert steps[0].error_code == "RUN_CANCELLED"

    await AgentRunFinalizer(
        runtime_factory(db_connection),
        None,
        worker_id=claimed_run.worker_id,
        attempt_count=claimed_run.attempt_count,
    ).finalize_cancelled(run.id)
    assert (await refresh_run(db_session, run.id)).status == "cancelled"


@pytest.mark.asyncio
async def test_process_shutdown_requeues_active_run_without_terminal_event(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = await create_user(db_session)
    run = await create_run(
        db_session,
        user_id,
        message="create a plan",
        key="shutdown-interrupt",
    )

    class CancelledGraph:
        async def execute(self, state: object) -> None:
            del state
            raise asyncio.CancelledError

    import asyncio

    monkeypatch.setattr(
        "app.agent.executor.GraphFactory.build",
        lambda *args, **kwargs: CancelledGraph(),
    )
    executor = AgentRunExecutor(runtime_factory(db_connection))
    executor._shutdown_run_ids.add(run.id)  # noqa: SLF001
    await executor.execute(run.id)

    interrupted = await refresh_run(db_session, run.id)
    assert interrupted.status == "pending"
    assert interrupted.error_code is None
    assert interrupted.worker_id is None
    assert interrupted.lease_expires_at is None
    requeued = await db_session.scalar(
        select(func.count())
        .select_from(AgentEvent)
        .where(AgentEvent.run_id == run.id, AgentEvent.event_type == "run.requeued")
    )
    assert requeued == 1


@pytest.mark.asyncio
async def test_idempotency_active_conflict_sse_resume_and_user_isolation(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    user_a = await create_user(db_session)
    user_b = await create_user(db_session)
    scheduler = ManualExecutor()
    service_a = AgentRunService(db_session, get_settings(), scheduler)
    first = await create_run(db_session, user_a, key="same-key", scheduler=scheduler)
    first_id = first.id
    repeated = await create_run(db_session, user_a, key="same-key", scheduler=scheduler)
    assert repeated.id == first.id
    with pytest.raises(AppError) as reused_key:
        await create_run(
            db_session,
            user_a,
            message="different request",
            key="same-key",
            scheduler=scheduler,
        )
    assert reused_key.value.code == "STATE_IDEMPOTENCY_KEY_REUSED"
    with pytest.raises(AppError) as conflict:
        await create_run(db_session, user_a, key="other-key", scheduler=scheduler)
    assert conflict.value.code == "STATE_RUN_ALREADY_ACTIVE"

    await AgentRunExecutor(runtime_factory(db_connection)).execute(first_id)
    first = await refresh_run(db_session, first_id)
    all_events = [
        chunk
        async for chunk in service_a.stream_events(
            run_id=first.id, user_id=user_a, after_sequence=0
        )
    ]
    resumed = [
        chunk
        async for chunk in service_a.stream_events(
            run_id=first.id, user_id=user_a, after_sequence=2
        )
    ]
    assert all_events[0].startswith("id: 1\n")
    assert resumed[0].startswith("id: 3\n")
    assert "event: run.completed" in all_events[-1]

    with pytest.raises(AppError) as hidden_run:
        await service_a.get(first.id, user_b)
    with pytest.raises(AppError) as hidden_plan:
        await PlanQueryService(db_session).get_active(user_b)
    other_tasks = await PlanQueryService(db_session).list_tasks(
        user_id=user_b,
        scheduled_date=product_today(),
        state=None,
        plan_id=None,
        limit=50,
    )
    assert hidden_run.value.status_code == HTTPStatus.NOT_FOUND
    assert hidden_plan.value.status_code == HTTPStatus.NOT_FOUND
    assert other_tasks.items == []


@pytest.mark.asyncio
async def test_startup_recovery_requeues_an_expired_lease(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    user_id = await create_user(db_session)
    run = await create_run(db_session, user_id, key="interrupted")
    run_id = run.id
    run.status = "running"
    run.worker_id = "dead-worker"
    run.heartbeat_at = datetime.now(UTC) - timedelta(minutes=2)
    run.lease_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    run.attempt_count = 1
    run.deadline_at = datetime.now(UTC) + timedelta(minutes=5)
    await db_session.flush()
    executor = AgentRunExecutor(runtime_factory(db_connection))

    assert await executor.recover_interrupted() == 1
    recovered = await refresh_run(db_session, run_id)
    requeue_count = await db_session.scalar(
        select(func.count())
        .select_from(AgentEvent)
        .where(
            AgentEvent.run_id == run_id,
            AgentEvent.event_type == "run.requeued",
        )
    )
    assert recovered.status == "pending"
    assert recovered.error_code is None
    assert recovered.worker_id is None
    assert recovered.lease_expires_at is None
    assert requeue_count == 1


@pytest.mark.asyncio
async def test_startup_recovery_fails_an_exhausted_lease_once(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    user_id = await create_user(db_session)
    run = await create_run(db_session, user_id, key="retry-exhausted")
    run_id = run.id
    run.status = "running"
    run.worker_id = "dead-worker"
    run.lease_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    run.attempt_count = 3
    run.deadline_at = datetime.now(UTC) + timedelta(minutes=5)
    await db_session.flush()

    executor = AgentRunExecutor(runtime_factory(db_connection), max_attempts=3)
    assert await executor.recover_interrupted() == 1

    recovered = await refresh_run(db_session, run_id)
    assert recovered.status == "failed"
    assert recovered.error_code == "AGENT_RETRY_EXHAUSTED"


@pytest.mark.asyncio
async def test_invalid_runtime_config_is_terminalized_once(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    user_id = await create_user(db_session)
    run = await create_run(db_session, user_id, key="invalid-config")
    run_id = run.id
    run.config_snapshot_json = {"broken": True}
    await db_session.flush()

    await AgentRunExecutor(runtime_factory(db_connection)).execute(run_id)

    recovered = await refresh_run(db_session, run_id)
    terminal_count = await db_session.scalar(
        select(func.count())
        .select_from(AgentEvent)
        .where(
            AgentEvent.run_id == run_id,
            AgentEvent.event_type.in_(
                ("run.completed", "run.degraded", "run.failed", "run.cancelled")
            ),
        )
    )
    assert recovered.status == "failed"
    assert recovered.error_code == "CONFIG_SNAPSHOT_INVALID"
    assert terminal_count == 1
