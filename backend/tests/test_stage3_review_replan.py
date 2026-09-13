"""Stage 3 Task, Review, and deterministic replanning acceptance tests."""

from datetime import timedelta
from http import HTTPStatus
from uuid import UUID

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.agent.executor import AgentRunExecutor
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.time import product_today
from app.models.agent_run import AgentEvent, AgentRun
from app.models.plan import Plan, Task
from app.models.review import Review
from app.schemas.agent_runs import RunInputSnapshot
from app.schemas.enums import AbandonedReason, TaskStatus
from app.schemas.plans import (
    TaskChecklistUpdateRequest,
    TaskUpdateRequest,
    TaskVerificationRequest,
)
from app.schemas.reviews import ReviewCreateRequest, ReviewUpdateRequest
from app.services.plans import PlanQueryService
from app.services.reviews import ReviewService
from tests.test_agent_runtime import (
    ManualExecutor,
    create_run,
    create_user,
    refresh_run,
    runtime_factory,
)
from tests.test_profile_api import bearer, guest_login, profile_body


async def generated_plan(
    connection: AsyncConnection,
    session: AsyncSession,
    user_id: UUID,
    *,
    key: str,
    message: str = "帮我制定未来五周的求职计划",
) -> tuple[AgentRun, Plan, list[Task]]:
    run = await create_run(session, user_id, key=key, message=message)
    await AgentRunExecutor(runtime_factory(connection)).execute(run.id)
    run = await refresh_run(session, run.id)
    plan = await session.scalar(select(Plan).where(Plan.source_run_id == run.id))
    assert plan is not None
    tasks = list(
        await session.scalars(
            select(Task).where(Task.plan_id == plan.id).order_by(Task.order_index)
        )
    )
    assert tasks
    return run, plan, tasks


def review_payload(plan_id: UUID, **values: object) -> ReviewCreateRequest:
    return ReviewCreateRequest(
        plan_id=plan_id,
        review_date=product_today(),
        mood=3,
        **values,
    )


async def settle_week(
    session: AsyncSession,
    *,
    user_id: UUID,
    tasks: list[Task],
) -> None:
    service = PlanQueryService(session)
    for original in tasks:
        task = await session.get(Task, original.id)
        assert task is not None
        if task.state != "pending":
            continue
        started = await service.update_task(
            task_id=task.id,
            user_id=user_id,
            payload=TaskUpdateRequest(state=TaskStatus.IN_PROGRESS, version=task.version),
        )
        await service.update_task(
            task_id=task.id,
            user_id=user_id,
            payload=TaskUpdateRequest(
                state=TaskStatus.COMPLETED,
                version=started.task.version,
                actual_minutes=20,
            ),
        )


def close_fixed_cycle(monkeypatch: pytest.MonkeyPatch, plan: Plan) -> None:
    """Advance only the Review use-case clock beyond the fixed cycle boundary."""
    monkeypatch.setattr(
        "app.services.reviews.product_today",
        lambda: plan.plan_date + timedelta(days=7),
    )


def test_task_update_schema_rejects_state_specific_field_mismatches() -> None:
    with pytest.raises(ValidationError):
        TaskUpdateRequest(state=TaskStatus.COMPLETED, version=1)
    with pytest.raises(ValidationError):
        TaskUpdateRequest(
            state=TaskStatus.ABANDONED,
            version=1,
            abandoned_reason=AbandonedReason.OTHER,
        )
    with pytest.raises(ValidationError):
        TaskUpdateRequest(
            state=TaskStatus.IN_PROGRESS,
            version=1,
            actual_minutes=20,
        )
    with pytest.raises(ValidationError):
        TaskUpdateRequest(state=TaskStatus.EXPIRED, version=1)


@pytest.mark.asyncio
async def test_task_checklist_is_reversible_and_gates_actual_time_prompt(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    user_id = await create_user(db_session)
    _, plan, tasks = await generated_plan(
        db_connection,
        db_session,
        user_id,
        key="task-checklist",
    )
    task = tasks[0]
    task.starter_action = "1. 选择文档类型与向量数据库 2. 形成技术选型文档并核对组件清单"
    plan.status = "archived"
    await db_session.flush()
    service = PlanQueryService(db_session)

    first = await service.update_task_checklist(
        task_id=task.id,
        user_id=user_id,
        payload=TaskChecklistUpdateRequest(
            version=task.version,
            step_index=0,
            step_completed=True,
        ),
    )
    assert first.task.state == TaskStatus.IN_PROGRESS
    assert first.task.execution_steps[0].completed is True
    assert first.task.completion_ready is False
    assert first.plan_status == "archived"

    second = await service.update_task_checklist(
        task_id=task.id,
        user_id=user_id,
        payload=TaskChecklistUpdateRequest(
            version=first.task.version,
            step_index=1,
            step_completed=True,
        ),
    )
    assert second.task.completion_ready is True
    assert second.task.verification_status == "ready"

    failed = await service.verify_task(
        task_id=task.id,
        user_id=user_id,
        payload=TaskVerificationRequest(
            version=second.task.version,
            passed=False,
        ),
    )
    assert failed.task.state == TaskStatus.IN_PROGRESS
    assert failed.task.verification_status == "failed"
    assert failed.task.execution_steps[0].completed is True

    completed = await service.verify_task(
        task_id=task.id,
        user_id=user_id,
        payload=TaskVerificationRequest(
            passed=True,
            version=failed.task.version,
            actual_minutes=35,
        ),
    )
    reopened = await service.update_task_checklist(
        task_id=task.id,
        user_id=user_id,
        payload=TaskChecklistUpdateRequest(
            version=completed.task.version,
            step_index=0,
            step_completed=False,
        ),
    )
    assert reopened.task.state == TaskStatus.IN_PROGRESS
    assert reopened.task.actual_minutes is None
    assert reopened.task.execution_steps[0].completed is False
    assert reopened.task.deliverable_verified is False
    assert reopened.task.verification_status == "not_ready"


@pytest.mark.asyncio
async def test_task_state_machine_optimistic_lock_and_plan_completion(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    user_id = await create_user(db_session)
    other_user_id = await create_user(db_session)
    _, plan, tasks = await generated_plan(
        db_connection,
        db_session,
        user_id,
        key="stage3-task-plan",
    )
    plan_id = plan.id
    last_task_id = tasks[-1].id
    service = PlanQueryService(db_session)

    with pytest.raises(AppError) as illegal:
        await service.update_task(
            task_id=tasks[0].id,
            user_id=user_id,
            payload=TaskUpdateRequest(
                state=TaskStatus.COMPLETED,
                version=1,
                actual_minutes=20,
            ),
        )
    assert illegal.value.code == "STATE_TASK_TRANSITION_INVALID"

    started = await service.update_task(
        task_id=tasks[0].id,
        user_id=user_id,
        payload=TaskUpdateRequest(state=TaskStatus.IN_PROGRESS, version=1),
    )
    assert started.task.version == 2
    assert started.plan_status.value == "active"

    with pytest.raises(AppError) as stale:
        await service.update_task(
            task_id=tasks[0].id,
            user_id=user_id,
            payload=TaskUpdateRequest(
                state=TaskStatus.COMPLETED,
                version=1,
                actual_minutes=20,
            ),
        )
    assert stale.value.code == "STATE_TASK_VERSION_CONFLICT"

    with pytest.raises(AppError) as hidden:
        await service.update_task(
            task_id=tasks[0].id,
            user_id=other_user_id,
            payload=TaskUpdateRequest(
                state=TaskStatus.COMPLETED,
                version=2,
                actual_minutes=20,
            ),
        )
    assert hidden.value.code == "NOT_FOUND_TASK"

    for index, task in enumerate(tasks):
        version = 2 if index == 0 else 1
        if index > 0:
            response = await service.update_task(
                task_id=task.id,
                user_id=user_id,
                payload=TaskUpdateRequest(
                    state=TaskStatus.IN_PROGRESS,
                    version=version,
                ),
            )
            version = response.task.version
        completed = await service.update_task(
            task_id=task.id,
            user_id=user_id,
            payload=TaskUpdateRequest(
                state=TaskStatus.COMPLETED,
                version=version,
                actual_minutes=25,
            ),
        )
        if index < len(tasks) - 1:
            assert completed.plan_status.value == "active"
    assert completed.plan_status.value == "completed"
    db_session.expire_all()
    completed_plan = await db_session.get(Plan, plan_id)
    assert completed_plan is not None
    assert completed_plan.status == "completed"
    assert completed_plan.adopted_at is not None
    assert completed_plan.completed_at is not None

    reopened = await service.update_task(
        task_id=last_task_id,
        user_id=user_id,
        payload=TaskUpdateRequest(
            state=TaskStatus.IN_PROGRESS,
            version=completed.task.version,
        ),
    )
    assert reopened.task.state.value == "in_progress"
    assert reopened.task.completed_at is None
    assert reopened.task.actual_minutes is None
    assert reopened.plan_status.value == "active"
    db_session.expire_all()
    reopened_plan = await db_session.get(Plan, plan_id)
    assert reopened_plan is not None
    assert reopened_plan.status == "active"
    assert reopened_plan.completed_at is None


@pytest.mark.asyncio
async def test_review_counts_rules_idempotency_listing_and_isolation(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    user_id = await create_user(db_session)
    other_user_id = await create_user(db_session)
    _, plan, tasks = await generated_plan(
        db_connection,
        db_session,
        user_id,
        key="stage3-review-plan",
    )
    task_service = PlanQueryService(db_session)
    first = await task_service.update_task(
        task_id=tasks[0].id,
        user_id=user_id,
        payload=TaskUpdateRequest(state=TaskStatus.IN_PROGRESS, version=1),
    )
    await task_service.update_task(
        task_id=tasks[0].id,
        user_id=user_id,
        payload=TaskUpdateRequest(
            state=TaskStatus.COMPLETED,
            version=first.task.version,
            actual_minutes=30,
        ),
    )
    await task_service.update_task(
        task_id=tasks[1].id,
        user_id=user_id,
        payload=TaskUpdateRequest(
            state=TaskStatus.ABANDONED,
            version=1,
            abandoned_reason=AbandonedReason.NO_TIME,
        ),
    )

    service = ReviewService(db_session, get_settings(), ManualExecutor())
    payload = review_payload(
        plan.id,
        blockers="部署环境一直报错",
        adjustment_request="明天任务少一点",
        free_text="接口已经跑通",
    )
    created = await service.create(
        user_id=user_id,
        payload=payload,
        idempotency_key="stage3-review",
    )
    repeated = await service.create(
        user_id=user_id,
        payload=payload,
        idempotency_key="stage3-review",
    )

    assert repeated.review_id == created.review_id
    assert created.completed_count == 1
    assert created.abandoned_count == 0
    assert created.suggested_replan is True
    assert created.next_plan_action.value == "adjust"
    assert created.replan_reason is not None
    assert "用户明确提出调整" in created.replan_reason

    with pytest.raises(AppError) as duplicate:
        await service.create(
            user_id=user_id,
            payload=payload,
            idempotency_key="stage3-review-other-key",
        )
    assert duplicate.value.code == "STATE_REVIEW_ALREADY_EXISTS"

    listed = await service.list_reviews(
        user_id=user_id,
        plan_id=plan.id,
        date_from=None,
        date_to=None,
        cursor=None,
        limit=10,
    )
    hidden = await service.list_reviews(
        user_id=other_user_id,
        plan_id=None,
        date_from=None,
        date_to=None,
        cursor=None,
        limit=10,
    )
    assert [item.review_id for item in listed.items] == [created.review_id]
    assert hidden.items == []


@pytest.mark.asyncio
async def test_adjust_next_plan_keeps_history_and_uses_review_context(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = await create_user(db_session)
    _, source, source_tasks = await generated_plan(
        db_connection,
        db_session,
        user_id,
        key="stage3-adjust-source",
    )
    source_id = source.id
    scheduler = ManualExecutor()
    service = ReviewService(db_session, get_settings(), scheduler)
    review = await service.create(
        user_id=user_id,
        payload=review_payload(
            source_id,
            blockers="部署步骤太大",
            adjustment_request="明天拆小一点",
        ),
        idempotency_key="stage3-adjust-review",
    )
    with pytest.raises(AppError) as open_cycle:
        await service.start_next_plan(
            review_id=review.review_id,
            user_id=user_id,
            idempotency_key="stage3-adjust-run-too-early",
        )
    assert open_cycle.value.code == "STATE_WEEKLY_CYCLE_OPEN"
    await settle_week(db_session, user_id=user_id, tasks=source_tasks)
    with pytest.raises(AppError) as completed_early:
        await service.start_next_plan(
            review_id=review.review_id,
            user_id=user_id,
            idempotency_key="stage3-adjust-run-still-too-early",
        )
    assert completed_early.value.code == "STATE_WEEKLY_CYCLE_OPEN"
    close_fixed_cycle(monkeypatch, source)
    started = await service.start_next_plan(
        review_id=review.review_id,
        user_id=user_id,
        idempotency_key="stage3-adjust-run",
    )
    repeated = await service.start_next_plan(
        review_id=review.review_id,
        user_id=user_id,
        idempotency_key="ignored-after-review-binding",
    )
    assert repeated.run_id == started.run_id
    assert started.replan_mode.value == "adjust"
    assert scheduler.submitted == [started.run_id]
    with pytest.raises(AppError) as consumed_update:
        await service.update_review(
            review_id=review.review_id,
            user_id=user_id,
            payload=ReviewUpdateRequest(version=1, mood=5),
        )
    assert consumed_update.value.code == "STATE_REVIEW_ALREADY_CONSUMED"
    with pytest.raises(AppError) as consumed_delete:
        await service.delete_review(review_id=review.review_id, user_id=user_id)
    assert consumed_delete.value.code == "STATE_REVIEW_ALREADY_CONSUMED"

    await AgentRunExecutor(runtime_factory(db_connection)).execute(started.run_id)
    run = await refresh_run(db_session, started.run_id)
    run_id = run.id
    db_session.expire_all()
    old_plan = await db_session.get(Plan, source_id)
    new_plan = await db_session.scalar(select(Plan).where(Plan.source_run_id == run_id))
    persisted_review = await db_session.get(Review, review.review_id)
    events = list(
        await db_session.scalars(
            select(AgentEvent).where(AgentEvent.run_id == run_id).order_by(AgentEvent.sequence)
        )
    )
    refreshed_run = await db_session.get(AgentRun, run_id)

    assert refreshed_run is not None
    assert refreshed_run.status == "completed"
    assert refreshed_run.source_review_id == review.review_id
    assert refreshed_run.input_snapshot_json is not None
    adjust_snapshot = RunInputSnapshot.model_validate(refreshed_run.input_snapshot_json)
    assert adjust_snapshot.source_review is not None
    assert adjust_snapshot.source_review.review_id == review.review_id
    assert "部署步骤太大" in adjust_snapshot.blockers
    assert old_plan is not None and old_plan.status == "archived"
    assert new_plan is not None
    assert new_plan.parent_plan_id == old_plan.id
    assert new_plan.plan_date == old_plan.plan_date + timedelta(days=7)
    assert new_plan.overall_direction == old_plan.overall_direction
    assert new_plan.adjustment_reason == "明天拆小一点"
    assert persisted_review is not None
    assert persisted_review.next_plan_run_id == run_id
    assert events[-1].event_type == "run.completed"
    assert (
        sum(
            event.event_type in {"run.completed", "run.degraded", "run.failed", "run.cancelled"}
            for event in events
        )
        == 1
    )

    history = await PlanQueryService(db_session).list_plans(
        user_id=user_id,
        status=None,
        date_from=None,
        date_to=None,
        cursor=None,
        limit=10,
    )
    assert {item.plan_id for item in history.items} == {old_plan.id, new_plan.id}


@pytest.mark.asyncio
async def test_continue_preserves_direction_and_failed_replan_does_not_archive_source(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    continue_user = await create_user(db_session)
    _, continue_source, continue_tasks = await generated_plan(
        db_connection,
        db_session,
        continue_user,
        key="stage3-continue-source",
    )
    continue_source_direction = continue_source.overall_direction
    await settle_week(db_session, user_id=continue_user, tasks=continue_tasks)
    close_fixed_cycle(monkeypatch, continue_source)
    continue_service = ReviewService(db_session, get_settings(), ManualExecutor())
    review = await continue_service.create(
        user_id=continue_user,
        payload=review_payload(continue_source.id),
        idempotency_key="stage3-continue-review",
    )
    started = await continue_service.start_next_plan(
        review_id=review.review_id,
        user_id=continue_user,
        idempotency_key="stage3-continue-run",
    )
    assert started.replan_mode.value == "continue"
    await AgentRunExecutor(runtime_factory(db_connection)).execute(started.run_id)
    run = await refresh_run(db_session, started.run_id)
    next_plan = await db_session.scalar(select(Plan).where(Plan.source_run_id == run.id))
    assert next_plan is not None
    assert next_plan.overall_direction == continue_source_direction
    assert next_plan.adjustment_reason is None

    failed_user = await create_user(db_session)
    _, failed_source, failed_tasks = await generated_plan(
        db_connection,
        db_session,
        failed_user,
        key="stage3-failed-source",
    )
    failed_source_id = failed_source.id
    await settle_week(db_session, user_id=failed_user, tasks=failed_tasks)
    close_fixed_cycle(monkeypatch, failed_source)
    failed_service = ReviewService(db_session, get_settings(), ManualExecutor())
    failed_review = await failed_service.create(
        user_id=failed_user,
        payload=review_payload(failed_source_id, free_text="[mock:persist-failure]"),
        idempotency_key="stage3-failed-review",
    )
    failed_started = await failed_service.start_next_plan(
        review_id=failed_review.review_id,
        user_id=failed_user,
        idempotency_key="stage3-failed-run",
    )
    await AgentRunExecutor(runtime_factory(db_connection)).execute(failed_started.run_id)
    await refresh_run(db_session, failed_started.run_id)
    db_session.expire_all()
    preserved_source = await db_session.get(Plan, failed_source_id)
    failed_run = await db_session.get(AgentRun, failed_started.run_id)

    assert failed_run is not None
    assert failed_run.status == "failed"
    assert failed_run.error_code == "PERSIST_TRANSACTION_FAILED"
    assert preserved_source is not None
    assert preserved_source.status == "completed"
    assert (
        await db_session.scalar(select(Plan).where(Plan.source_run_id == failed_started.run_id))
        is None
    )


@pytest.mark.asyncio
async def test_adjust_replan_preserves_completed_facts_without_rescheduling_them(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = await create_user(db_session)
    _, source, tasks = await generated_plan(
        db_connection,
        db_session,
        user_id,
        key="stage3-completed-facts-source",
    )
    completed_deliverable = tasks[0].deliverable
    completed_task_id = tasks[0].id
    task_service = PlanQueryService(db_session)
    started = await task_service.update_task(
        task_id=tasks[0].id,
        user_id=user_id,
        payload=TaskUpdateRequest(state=TaskStatus.IN_PROGRESS, version=1),
    )
    await task_service.update_task(
        task_id=tasks[0].id,
        user_id=user_id,
        payload=TaskUpdateRequest(
            state=TaskStatus.COMPLETED,
            version=started.task.version,
            actual_minutes=30,
        ),
    )
    await task_service.update_task(
        task_id=tasks[1].id,
        user_id=user_id,
        payload=TaskUpdateRequest(
            state=TaskStatus.ABANDONED,
            version=1,
            abandoned_reason=AbandonedReason.BLOCKED,
        ),
    )
    await settle_week(db_session, user_id=user_id, tasks=tasks)
    close_fixed_cycle(monkeypatch, source)
    service = ReviewService(db_session, get_settings(), ManualExecutor())
    review = await service.create(
        user_id=user_id,
        payload=review_payload(
            source.id,
            blockers="项目环境阻塞",
            adjustment_request="换一个更小的交付物",
        ),
        idempotency_key="stage3-completed-facts-review",
    )
    next_run = await service.start_next_plan(
        review_id=review.review_id,
        user_id=user_id,
        idempotency_key="stage3-completed-facts-run",
    )
    await AgentRunExecutor(runtime_factory(db_connection)).execute(next_run.run_id)
    run = await refresh_run(db_session, next_run.run_id)
    new_plan = await db_session.scalar(select(Plan).where(Plan.source_run_id == run.id))
    assert new_plan is not None
    new_tasks = list(await db_session.scalars(select(Task).where(Task.plan_id == new_plan.id)))

    assert run.input_snapshot_json is not None
    completed_snapshot = RunInputSnapshot.model_validate(run.input_snapshot_json)
    assert completed_deliverable in completed_snapshot.completed_facts
    assert {item.state.value for item in completed_snapshot.recent_tasks} >= {
        "completed",
        "abandoned",
    }
    assert completed_deliverable not in {task.deliverable for task in new_tasks}
    assert (
        await db_session.scalar(
            select(Task).where(
                Task.id == completed_task_id,
                Task.state == "completed",
            )
        )
        is not None
    )


@pytest.mark.asyncio
async def test_stage3_http_contract_and_identity_isolation(
    api_client: AsyncClient,
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    token, user_id_text, _ = await guest_login(api_client)
    other_token, _, _ = await guest_login(api_client)
    await api_client.put(
        "/api/v1/profile",
        json=profile_body(),
        headers={**bearer(token), "Idempotency-Key": "stage3-api-profile"},
    )
    user_id = UUID(user_id_text)
    _, plan, tasks = await generated_plan(
        db_connection,
        db_session,
        user_id,
        key="stage3-api-plan",
    )

    started = await api_client.patch(
        f"/api/v1/tasks/{tasks[0].id}",
        json={"state": "in_progress", "version": 1},
        headers=bearer(token),
    )
    stale = await api_client.patch(
        f"/api/v1/tasks/{tasks[0].id}",
        json={"state": "completed", "version": 1, "actual_minutes": 20},
        headers=bearer(token),
    )
    hidden = await api_client.patch(
        f"/api/v1/tasks/{tasks[0].id}",
        json={"state": "completed", "version": 2, "actual_minutes": 20},
        headers=bearer(other_token),
    )
    review = await api_client.post(
        "/api/v1/reviews",
        json={
            "plan_id": str(plan.id),
            "review_date": str(product_today()),
            "mood": 4,
        },
        headers={**bearer(token), "Idempotency-Key": "stage3-api-review"},
    )
    review_id = review.json()["review_id"]
    detail = await api_client.get(f"/api/v1/reviews/{review_id}", headers=bearer(token))
    updated = await api_client.patch(
        f"/api/v1/reviews/{review_id}",
        json={"version": 1, "mood": 5, "free_text": "补充复盘内容"},
        headers=bearer(token),
    )
    stale_review = await api_client.patch(
        f"/api/v1/reviews/{review_id}",
        json={"version": 1, "mood": 2},
        headers=bearer(token),
    )
    me = await api_client.get("/api/v1/me", headers=bearer(token))
    other_reviews = await api_client.get("/api/v1/reviews", headers=bearer(other_token))
    hidden_delete = await api_client.delete(
        f"/api/v1/reviews/{review_id}", headers=bearer(other_token)
    )
    deleted = await api_client.delete(
        f"/api/v1/reviews/{review_id}", headers=bearer(token)
    )
    missing = await api_client.get(f"/api/v1/reviews/{review_id}", headers=bearer(token))

    assert started.status_code == HTTPStatus.OK
    assert started.json()["plan_status"] == "active"
    assert stale.status_code == HTTPStatus.CONFLICT
    assert stale.json()["error"]["code"] == "STATE_TASK_VERSION_CONFLICT"
    assert hidden.status_code == HTTPStatus.NOT_FOUND
    assert review.status_code == HTTPStatus.CREATED
    assert review.json()["next_plan_action"] == "continue"
    assert review.json()["version"] == 1
    assert detail.status_code == HTTPStatus.OK
    assert updated.status_code == HTTPStatus.OK
    assert updated.json()["mood"] == 5
    assert updated.json()["free_text"] == "补充复盘内容"
    assert updated.json()["version"] == 2
    assert stale_review.status_code == HTTPStatus.CONFLICT
    assert stale_review.json()["error"]["code"] == "STATE_REVIEW_VERSION_CONFLICT"
    assert me.status_code == HTTPStatus.OK
    assert me.json()["latest_review"] == updated.json()
    assert other_reviews.status_code == HTTPStatus.OK
    assert other_reviews.json()["items"] == []
    assert hidden_delete.status_code == HTTPStatus.NOT_FOUND
    assert deleted.status_code == HTTPStatus.NO_CONTENT
    assert missing.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_task_detail_manual_edit_and_confirmed_ai_proposal(
    api_client: AsyncClient,
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    token, user_id_text, _ = await guest_login(api_client)
    await api_client.put(
        "/api/v1/profile",
        json=profile_body(time_budget_minutes=60),
        headers={**bearer(token), "Idempotency-Key": "task-adjustment-profile"},
    )
    _, plan, tasks = await generated_plan(
        db_connection,
        db_session,
        UUID(user_id_text),
        key="task-adjustment-plan",
    )

    detail = await api_client.get(
        f"/api/v1/tasks/{tasks[0].id}", headers=bearer(token)
    )
    assert detail.status_code == HTTPStatus.OK
    assert detail.json()["editable"] is True
    assert detail.json()["week_focus"]

    over_budget = await api_client.patch(
        f"/api/v1/tasks/{tasks[0].id}/details",
        json={"version": 1, "estimated_minutes": 90},
        headers={**bearer(token), "Idempotency-Key": "manual-over-budget"},
    )
    assert over_budget.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert over_budget.json()["error"]["code"] == "VALIDATION_TASK_DAILY_BUDGET"

    edited = await api_client.patch(
        f"/api/v1/tasks/{tasks[0].id}/details",
        json={
            "version": 1,
            "title": "先完成最小可验证版本",
            "estimated_minutes": 45,
        },
        headers={**bearer(token), "Idempotency-Key": "manual-edit-task"},
    )
    assert edited.status_code == HTTPStatus.OK
    assert edited.json()["task"]["title"] == "先完成最小可验证版本"
    assert edited.json()["task"]["version"] == 2

    proposal = await api_client.post(
        f"/api/v1/tasks/{tasks[1].id}/adjustment-proposals",
        json={"version": 1, "message": "这个任务太难了，我今天只有 20 分钟"},
        headers={**bearer(token), "Idempotency-Key": "ai-propose-task"},
    )
    assert proposal.status_code == HTTPStatus.CREATED
    proposal_body = proposal.json()
    assert proposal_body["status"] == "pending"
    assert proposal_body["proposed_patch"]["estimated_minutes"] == 20

    before_confirm = await api_client.get(
        f"/api/v1/tasks/{tasks[1].id}", headers=bearer(token)
    )
    assert before_confirm.json()["task"]["estimated_minutes"] != 20

    confirmed = await api_client.post(
        f"/api/v1/task-adjustment-proposals/{proposal_body['adjustment_id']}/confirm",
        json={"version": proposal_body["version"]},
        headers=bearer(token),
    )
    assert confirmed.status_code == HTTPStatus.OK
    assert confirmed.json()["task"]["estimated_minutes"] == 20
    assert confirmed.json()["task"]["version"] == 2

    started = await api_client.patch(
        f"/api/v1/tasks/{tasks[1].id}",
        json={"state": "in_progress", "version": 2},
        headers=bearer(token),
    )
    assert started.status_code == HTTPStatus.OK
    edit_while_in_progress = await api_client.patch(
        f"/api/v1/tasks/{tasks[1].id}/details",
        json={"version": 3, "title": "进行中仍可调整"},
        headers={**bearer(token), "Idempotency-Key": "manual-after-start"},
    )
    assert edit_while_in_progress.status_code == HTTPStatus.OK
    assert edit_while_in_progress.json()["task"]["version"] == 4

    completed = await api_client.patch(
        f"/api/v1/tasks/{tasks[1].id}",
        json={"state": "completed", "version": 4, "actual_minutes": 20},
        headers=bearer(token),
    )
    assert completed.status_code == HTTPStatus.OK
    completed_detail = await api_client.get(
        f"/api/v1/tasks/{tasks[1].id}", headers=bearer(token)
    )
    assert completed_detail.json()["editable"] is False
    assert completed_detail.json()["edit_reason"] == "Only an unfinished Task can be edited"

    plan_response = await api_client.get(
        f"/api/v1/plans/{plan.id}", headers=bearer(token)
    )
    assert len(plan_response.json()["tasks"]) == 7
