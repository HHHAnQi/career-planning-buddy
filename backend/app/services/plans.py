"""Plan and Task query and Stage 3 state-transition use cases."""

from datetime import UTC, date, datetime
from http import HTTPStatus
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import session_transaction
from app.core.exceptions import AppError
from app.core.time import product_today
from app.models.plan import Plan, Task
from app.repositories.evidence import EvidenceRepository
from app.repositories.plans import PlanRepository
from app.schemas.agent_runs import WeeklyFocusCandidate
from app.schemas.enums import AbandonedReason, PlanStatus, TaskStatus, TaskType
from app.schemas.plans import (
    ActivePlanResponse,
    PlanListResponse,
    PlanSourceResponse,
    PlanSourcesResponse,
    TaskChecklistUpdateRequest,
    TaskListResponse,
    TaskResponse,
    TaskUpdateRequest,
    TaskUpdateResponse,
    TaskVerificationRequest,
)
from app.services.task_progress import fixed_cycle_contains, parse_execution_steps


class PlanQueryService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._plans = PlanRepository(session)

    async def get_active(self, user_id: UUID) -> ActivePlanResponse:
        async with session_transaction(self._session):
            plan = await self._plans.get_current_cycle_for_user(user_id)
            if plan is None:
                plan = await self._plans.get_latest_completed_for_user(user_id)
            if plan is None:
                raise AppError(
                    code="NOT_FOUND_PLAN",
                    message="active Plan was not found",
                    status_code=HTTPStatus.NOT_FOUND,
                )
            tasks = await self._plans.tasks_for_plan(plan.id, user_id)
            companion = await self._plans.companion_for_plan(plan.id, user_id)
            sources = await self._plan_sources(plan, user_id)
            return self._plan_response(
                plan,
                tasks,
                companion.message if companion else None,
                sources,
            )

    async def get_plan(self, plan_id: UUID, user_id: UUID) -> ActivePlanResponse:
        async with session_transaction(self._session):
            plan = await self._plans.get_for_user(plan_id, user_id)
            if plan is None:
                raise AppError(
                    code="NOT_FOUND_PLAN",
                    message="Plan was not found",
                    status_code=HTTPStatus.NOT_FOUND,
                )
            tasks = await self._plans.tasks_for_plan(plan.id, user_id)
            companion = await self._plans.companion_for_plan(plan.id, user_id)
            sources = await self._plan_sources(plan, user_id)
            return self._plan_response(
                plan, tasks, companion.message if companion else None, sources
            )

    async def get_sources(self, plan_id: UUID, user_id: UUID) -> PlanSourcesResponse:
        async with session_transaction(self._session):
            plan = await self._plans.get_for_user(plan_id, user_id)
            if plan is None:
                raise AppError(
                    code="NOT_FOUND_PLAN",
                    message="Plan was not found",
                    status_code=HTTPStatus.NOT_FOUND,
                )
            return PlanSourcesResponse(items=await self._plan_sources(plan, user_id))

    async def list_plans(
        self,
        *,
        user_id: UUID,
        status: str | None,
        date_from: date | None,
        date_to: date | None,
        cursor: UUID | None,
        limit: int,
    ) -> PlanListResponse:
        async with session_transaction(self._session):
            plans = await self._plans.list_plans(
                user_id,
                status=status,
                date_from=date_from,
                date_to=date_to,
                cursor=cursor,
                limit=limit + 1,
            )
            has_more = len(plans) > limit
            selected = plans[:limit]
            items: list[ActivePlanResponse] = []
            for plan in selected:
                tasks = await self._plans.tasks_for_plan(plan.id, user_id)
                companion = await self._plans.companion_for_plan(plan.id, user_id)
                sources = await self._plan_sources(plan, user_id)
                items.append(
                    self._plan_response(
                        plan,
                        tasks,
                        companion.message if companion else None,
                        sources,
                    )
                )
            return PlanListResponse(
                items=items,
                next_cursor=selected[-1].id if has_more and selected else None,
            )

    async def list_tasks(
        self,
        *,
        user_id: UUID,
        scheduled_date: date | None,
        state: str | None,
        plan_id: UUID | None,
        limit: int,
    ) -> TaskListResponse:
        async with session_transaction(self._session):
            tasks = await self._plans.list_tasks(
                user_id,
                scheduled_date=scheduled_date or product_today(),
                state=state,
                plan_id=plan_id,
                limit=limit,
            )
            return TaskListResponse(items=[self.to_task_response(task) for task in tasks])

    async def update_task(
        self,
        *,
        task_id: UUID,
        user_id: UUID,
        payload: TaskUpdateRequest,
    ) -> TaskUpdateResponse:
        async with session_transaction(self._session):
            task = await self._plans.get_task_for_user(task_id, user_id, for_update=True)
            if task is None:
                raise AppError(
                    code="NOT_FOUND_TASK",
                    message="Task was not found",
                    status_code=HTTPStatus.NOT_FOUND,
                )
            if task.version != payload.version:
                raise AppError(
                    code="STATE_TASK_VERSION_CONFLICT",
                    message="Task version does not match the current version",
                    status_code=HTTPStatus.CONFLICT,
                    details={"current_version": task.version},
                )
            plan = await self._plans.get_for_user(task.plan_id, user_id, for_update=True)
            if plan is None:
                raise AppError(
                    code="NOT_FOUND_PLAN",
                    message="Plan was not found",
                    status_code=HTTPStatus.NOT_FOUND,
                )
            reopening_task = (
                task.state == "completed" and payload.state == TaskStatus.IN_PROGRESS
            )
            reopening_plan = plan.status == "completed" and reopening_task
            legacy_current_cycle = self._is_current_archived_cycle(plan)
            if (
                plan.status not in {"generated", "active"}
                and not reopening_plan
                and not legacy_current_cycle
            ):
                raise AppError(
                    code="STATE_PLAN_NOT_MUTABLE",
                    message="Tasks can only be updated for a generated or active Plan",
                    status_code=HTTPStatus.CONFLICT,
                )
            target = payload.state.value
            allowed = {
                "pending": {"in_progress", "abandoned"},
                "in_progress": {"completed", "abandoned"},
                "completed": {"in_progress"},
            }
            if target not in allowed.get(task.state, set()):
                raise AppError(
                    code="STATE_TASK_TRANSITION_INVALID",
                    message=f"Task cannot transition from {task.state} to {target}",
                    status_code=HTTPStatus.CONFLICT,
                )

            now = datetime.now(UTC)
            task.state = target
            task.version += 1
            task.updated_at = now
            task.actual_minutes = payload.actual_minutes
            task.abandoned_reason = (
                payload.abandoned_reason.value if payload.abandoned_reason is not None else None
            )
            task.abandoned_reason_text = payload.abandoned_reason_text
            if target == "in_progress":
                task.completed_at = None
                task.actual_minutes = None
                task.started_at = task.started_at or now
                if reopening_task:
                    task.deliverable_verified = False
                    task.verification_status = (
                        "ready" if self._all_execution_steps_completed(task) else "not_ready"
                    )
                if plan.status in {"generated", "completed"}:
                    plan.status = "active"
                    plan.completed_at = None
                    plan.adopted_at = now
                    plan.updated_at = now
                    plan.version += 1
                companion_message = (
                    f"已将「{task.title}」恢复为进行中，可以继续完善后再完成。"
                    if reopening_task
                    else f"已经开始「{task.title}」，先完成第一个可验证动作。"
                )
            elif target == "completed":
                task.completed_step_indexes_json = list(
                    range(len(parse_execution_steps(task.starter_action)))
                )
                task.deliverable_verified = True
                task.verification_status = "passed"
                task.completed_at = now
                companion_message = "你已经完成了这一步，今天的推进已经成为可验证的成果。"
                await self._plans.create_companion(
                    user_id=user_id,
                    plan_id=plan.id,
                    task_id=task.id,
                    trigger_tag="task_completed",
                    message=companion_message,
                    template_version="task_completed_v1",
                )
            else:
                task.deliverable_verified = False
                task.verification_status = "not_ready"
                task.abandoned_at = now
                companion_message = self._abandoned_companion(payload.abandoned_reason)
                await self._plans.create_companion(
                    user_id=user_id,
                    plan_id=plan.id,
                    task_id=task.id,
                    trigger_tag="task_abandoned",
                    message=companion_message,
                    template_version="task_abandoned_v1",
                )
            await self._session.flush()

            if target in {"completed", "abandoned"}:
                counts = await self._plans.task_state_counts(
                    user_id=user_id,
                    plan_id=plan.id,
                    scheduled_date=None,
                )
                total = sum(counts.values())
                settled = sum(
                    counts.get(state, 0) for state in ("completed", "abandoned", "expired")
                )
                if total > 0 and settled == total:
                    plan.status = "completed"
                    plan.completed_at = now
                    plan.updated_at = now
                    plan.version += 1
                    await self._session.flush()

            return TaskUpdateResponse(
                task=self.to_task_response(task),
                plan_status=PlanStatus(plan.status),
                companion_message=companion_message,
            )

    async def update_task_checklist(
        self,
        *,
        task_id: UUID,
        user_id: UUID,
        payload: TaskChecklistUpdateRequest,
    ) -> TaskUpdateResponse:
        async with session_transaction(self._session):
            task = await self._plans.get_task_for_user(task_id, user_id, for_update=True)
            if task is None:
                raise AppError(
                    code="NOT_FOUND_TASK",
                    message="Task was not found",
                    status_code=HTTPStatus.NOT_FOUND,
                )
            if task.version != payload.version:
                raise AppError(
                    code="STATE_TASK_VERSION_CONFLICT",
                    message="Task version does not match the current version",
                    status_code=HTTPStatus.CONFLICT,
                    details={"current_version": task.version},
                )
            plan = await self._plans.get_for_user(task.plan_id, user_id, for_update=True)
            if plan is None:
                raise AppError(
                    code="NOT_FOUND_PLAN",
                    message="Plan was not found",
                    status_code=HTTPStatus.NOT_FOUND,
                )
            reopening = task.state == "completed"
            if (
                plan.status not in {"generated", "active"}
                and not (reopening and plan.status == "completed")
                and not self._is_current_archived_cycle(plan)
            ):
                raise AppError(
                    code="STATE_PLAN_NOT_MUTABLE",
                    message="Checklist can only be updated for the current Plan",
                    status_code=HTTPStatus.CONFLICT,
                )
            if task.state in {"abandoned", "expired"}:
                raise AppError(
                    code="STATE_TASK_CHECKLIST_NOT_MUTABLE",
                    message="Checklist cannot be updated for this Task state",
                    status_code=HTTPStatus.CONFLICT,
                )

            steps = parse_execution_steps(task.starter_action)
            completed = (
                set(range(len(steps)))
                if task.state == "completed"
                else {
                    index
                    for index in task.completed_step_indexes_json
                    if isinstance(index, int) and 0 <= index < len(steps)
                }
            )
            if payload.step_index >= len(steps):
                raise AppError(
                    code="VALIDATION_TASK_STEP_INDEX",
                    message="Execution step does not exist",
                    status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                )
            before = payload.step_index in completed
            if payload.step_completed:
                completed.add(payload.step_index)
            else:
                completed.discard(payload.step_index)
            changed = before != payload.step_completed

            now = datetime.now(UTC)
            task.completed_step_indexes_json = sorted(completed)
            all_steps_completed = len(completed) == len(steps)
            if changed:
                task.deliverable_verified = False
                task.verification_status = "ready" if all_steps_completed else "not_ready"
            task.version += 1
            task.updated_at = now
            if task.state == "pending" and completed:
                task.state = "in_progress"
                task.started_at = now
            if reopening and changed:
                task.state = "in_progress"
                task.completed_at = None
                task.actual_minutes = None
            if task.state == "in_progress" and plan.status in {"generated", "completed"}:
                plan.status = "active"
                plan.completed_at = None
                plan.adopted_at = plan.adopted_at or now
                plan.updated_at = now
                plan.version += 1
            await self._session.flush()

            message = (
                "执行步骤已完成，可以按照验收标准确认成果。"
                if all_steps_completed and task.state != "completed"
                else "执行进度已更新。"
            )
            return TaskUpdateResponse(
                task=self.to_task_response(task),
                plan_status=PlanStatus(plan.status),
                companion_message=message,
            )

    async def verify_task(
        self,
        *,
        task_id: UUID,
        user_id: UUID,
        payload: TaskVerificationRequest,
    ) -> TaskUpdateResponse:
        async with session_transaction(self._session):
            task = await self._plans.get_task_for_user(task_id, user_id, for_update=True)
            if task is None:
                raise AppError(
                    code="NOT_FOUND_TASK",
                    message="Task was not found",
                    status_code=HTTPStatus.NOT_FOUND,
                )
            if task.version != payload.version:
                raise AppError(
                    code="STATE_TASK_VERSION_CONFLICT",
                    message="Task version does not match the current version",
                    status_code=HTTPStatus.CONFLICT,
                    details={"current_version": task.version},
                )
            plan = await self._plans.get_for_user(task.plan_id, user_id, for_update=True)
            if plan is None:
                raise AppError(
                    code="NOT_FOUND_PLAN",
                    message="Plan was not found",
                    status_code=HTTPStatus.NOT_FOUND,
                )
            if plan.status not in {"generated", "active"} and not self._is_current_archived_cycle(
                plan
            ):
                raise AppError(
                    code="STATE_PLAN_NOT_MUTABLE",
                    message="Verification is only available for the current Plan cycle",
                    status_code=HTTPStatus.CONFLICT,
                )
            if task.state not in {"pending", "in_progress"}:
                raise AppError(
                    code="STATE_TASK_NOT_VERIFIABLE",
                    message="Only an unfinished Task can be verified",
                    status_code=HTTPStatus.CONFLICT,
                )
            if not self._all_execution_steps_completed(task):
                raise AppError(
                    code="STATE_TASK_EXECUTION_INCOMPLETE",
                    message="Complete every execution step before verification",
                    status_code=HTTPStatus.CONFLICT,
                )

            now = datetime.now(UTC)
            task.version += 1
            task.updated_at = now
            task.started_at = task.started_at or now
            if payload.passed:
                task.state = "completed"
                task.verification_status = "passed"
                task.deliverable_verified = True
                task.actual_minutes = payload.actual_minutes
                task.completed_at = now
                companion_message = "验收已经通过，这项今日任务已完成。"
                await self._plans.create_companion(
                    user_id=user_id,
                    plan_id=plan.id,
                    task_id=task.id,
                    trigger_tag="task_completed",
                    message=companion_message,
                    template_version="task_verified_v1",
                )
            else:
                task.state = "in_progress"
                task.verification_status = "failed"
                task.deliverable_verified = False
                task.actual_minutes = None
                task.completed_at = None
                companion_message = "已记录验收未通过，执行步骤保留，可以继续完善成果。"

            if task.state == "in_progress" and plan.status == "generated":
                plan.status = "active"
                plan.adopted_at = plan.adopted_at or now
                plan.updated_at = now
                plan.version += 1
            await self._session.flush()

            if payload.passed:
                counts = await self._plans.task_state_counts(
                    user_id=user_id,
                    plan_id=plan.id,
                    scheduled_date=None,
                )
                total = sum(counts.values())
                settled = sum(
                    counts.get(state, 0) for state in ("completed", "abandoned", "expired")
                )
                if total > 0 and settled == total:
                    plan.status = "completed"
                    plan.completed_at = now
                    plan.updated_at = now
                    plan.version += 1
                    await self._session.flush()

            return TaskUpdateResponse(
                task=self.to_task_response(task),
                plan_status=PlanStatus(plan.status),
                companion_message=companion_message,
            )

    @staticmethod
    def _all_execution_steps_completed(task: Task) -> bool:
        steps = parse_execution_steps(task.starter_action)
        completed = {
            index
            for index in task.completed_step_indexes_json
            if isinstance(index, int) and 0 <= index < len(steps)
        }
        return len(completed) == len(steps)

    @staticmethod
    def _is_current_archived_cycle(plan: Plan) -> bool:
        return plan.status == "archived" and fixed_cycle_contains(
            plan_date=plan.plan_date,
            horizon_end=plan.horizon_end,
            target=product_today(),
        )

    @staticmethod
    def _abandoned_companion(reason: AbandonedReason | None) -> str:
        if reason == AbandonedReason.NO_TIME:
            return "时间不足不是失败，复盘时可以把下一步拆得更小。"
        if reason == AbandonedReason.BLOCKED:
            return "阻碍已经记录，复盘时会据此调整下一步。"
        return "这次放弃已经记录，复盘会帮助我们选择更合适的下一步。"

    @classmethod
    def _plan_response(
        cls,
        plan: Plan,
        tasks: list[Task],
        companion_message: str | None,
        sources: list[PlanSourceResponse],
    ) -> ActivePlanResponse:
        return ActivePlanResponse(
            plan_id=plan.id,
            status=PlanStatus(plan.status),
            plan_date=plan.plan_date,
            horizon_start=plan.horizon_start,
            horizon_end=plan.horizon_end,
            overall_direction=plan.overall_direction,
            weekly_focus=[
                WeeklyFocusCandidate.model_validate(item) for item in plan.weekly_focus_json
            ],
            summary=plan.summary,
            rationale=plan.rationale,
            adjustment_reason=plan.adjustment_reason,
            sources=sources,
            tasks=[cls.to_task_response(task) for task in tasks],
            companion_message=companion_message,
            version=plan.version,
            adopted_at=plan.adopted_at,
            created_at=plan.created_at,
        )

    async def _plan_sources(self, plan: Plan, user_id: UUID) -> list[PlanSourceResponse]:
        references = plan.evidence_refs_json
        memory_ids = [
            UUID(str(item["id"]))
            for item in references
            if item.get("kind") == "memory" and item.get("id")
        ]
        atom_ids = [
            UUID(str(item["id"]))
            for item in references
            if item.get("kind") == "experience_atom" and item.get("id")
        ]
        source_ids = [
            UUID(str(item["id"]))
            for item in references
            if item.get("kind") == "search_source" and item.get("id")
        ]
        repository = EvidenceRepository(self._session)
        memories = {
            item.id: item for item in await repository.resolve_memories(user_id, memory_ids)
        }
        atoms = {item.id: item for item in await repository.resolve_atoms(atom_ids)}
        sources = {
            item.id: item
            for item in await repository.resolve_sources(plan.source_run_id, source_ids)
        }
        result: list[PlanSourceResponse] = []
        for reference in references:
            kind = str(reference.get("kind"))
            try:
                item_id = UUID(str(reference.get("id")))
            except ValueError:
                continue
            if kind == "memory":
                memory = memories.get(item_id)
                result.append(
                    PlanSourceResponse(
                        kind="memory",
                        id=item_id,
                        available=memory is not None and memory.status == "active",
                        title=memory.memory_type if memory else None,
                        snippet=memory.summary if memory and memory.status == "active" else None,
                        reliability=0.9 if memory else None,
                    )
                )
            elif kind == "experience_atom":
                atom = atoms.get(item_id)
                reliability = atom.evidence_json.get("reliability") if atom else None
                result.append(
                    PlanSourceResponse(
                        kind="experience_atom",
                        id=item_id,
                        available=atom is not None and atom.is_active,
                        title=atom.title if atom else None,
                        snippet=atom.content if atom and atom.is_active else None,
                        reliability=(
                            float(reliability) if isinstance(reliability, (int, float)) else None
                        ),
                    )
                )
            elif kind == "search_source":
                source = sources.get(item_id)
                result.append(
                    PlanSourceResponse(
                        kind="search_source",
                        id=item_id,
                        available=source is not None,
                        title=source.title if source else None,
                        url=source.url if source else None,
                        snippet=source.snippet if source else None,
                        reliability=float(source.reliability) if source else None,
                    )
                )
        return result

    @staticmethod
    def to_task_response(task: Task) -> TaskResponse:
        steps = parse_execution_steps(task.starter_action)
        completed_indexes = (
            set(range(len(steps)))
            if task.state == "completed"
            else {
                index
                for index in task.completed_step_indexes_json
                if isinstance(index, int) and 0 <= index < len(steps)
            }
        )
        deliverable_verified = task.state == "completed" or task.deliverable_verified
        all_steps_completed = len(completed_indexes) == len(steps)
        verification_status = (
            "passed"
            if task.state == "completed"
            else "not_ready"
            if not all_steps_completed
            else "ready"
            if task.verification_status == "not_ready"
            else task.verification_status
        )
        return TaskResponse(
            task_id=task.id,
            plan_id=task.plan_id,
            title=task.title,
            task_type=TaskType(task.task_type),
            scheduled_date=task.scheduled_date,
            order_index=task.order_index,
            state=TaskStatus(task.state),
            starter_action=task.starter_action,
            execution_steps=[
                {"index": index, "text": text, "completed": index in completed_indexes}
                for index, text in enumerate(steps)
            ],
            deliverable=task.deliverable,
            deliverable_verified=deliverable_verified,
            verification_status=verification_status,
            completion_ready=(all_steps_completed and task.state != "completed"),
            rationale=task.rationale,
            estimated_minutes=task.estimated_minutes,
            actual_minutes=task.actual_minutes,
            abandoned_reason=(
                AbandonedReason(task.abandoned_reason)
                if task.abandoned_reason is not None
                else None
            ),
            abandoned_reason_text=task.abandoned_reason_text,
            version=task.version,
            started_at=task.started_at,
            completed_at=task.completed_at,
            abandoned_at=task.abandoned_at,
            created_at=task.created_at,
        )
