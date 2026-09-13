"""Fixed-cycle Task detail, manual editing, and confirmed AI adjustment use cases."""

from datetime import UTC, datetime
from http import HTTPStatus
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import session_transaction
from app.core.exceptions import AppError
from app.core.time import product_today
from app.models.plan import Plan, Task, TaskAdjustmentProposal
from app.providers.task_adjustment import TaskAdjustmentProvider
from app.repositories.plans import PlanRepository
from app.repositories.profiles import ProfileRepository
from app.repositories.task_adjustments import TaskAdjustmentRepository
from app.schemas.plans import (
    TaskAdjustmentCreateRequest,
    TaskAdjustmentProposalResponse,
    TaskDetailResponse,
    TaskEditFields,
    TaskEditRequest,
    TaskEditResponse,
)
from app.services.plans import PlanQueryService
from app.services.task_progress import fixed_cycle_contains


class TaskAdjustmentService:
    def __init__(
        self,
        session: AsyncSession,
        provider: TaskAdjustmentProvider,
    ) -> None:
        self._session = session
        self._provider = provider
        self._plans = PlanRepository(session)
        self._profiles = ProfileRepository(session)
        self._adjustments = TaskAdjustmentRepository(session)

    async def get_detail(self, *, task_id: UUID, user_id: UUID) -> TaskDetailResponse:
        async with session_transaction(self._session):
            task, plan = await self._task_and_plan(task_id, user_id)
            focus, signal = self._week_focus(plan)
            editable, reason = self._editability(task, plan)
            return TaskDetailResponse(
                task=PlanQueryService.to_task_response(task),
                week_focus=focus,
                week_success_signal=signal,
                editable=editable,
                edit_reason=reason,
            )

    async def edit_manual(
        self,
        *,
        task_id: UUID,
        user_id: UUID,
        payload: TaskEditRequest,
        idempotency_key: str,
    ) -> TaskEditResponse:
        async with session_transaction(self._session):
            existing = await self._adjustments.get_by_idempotency(user_id, idempotency_key)
            if existing is not None:
                if (
                    existing.task_id != task_id
                    or existing.generation_method != "manual"
                    or existing.status != "applied"
                ):
                    raise self._idempotency_conflict()
                task = await self._plans.get_task_for_user(task_id, user_id)
                if task is None:
                    raise self._not_found_task()
                return TaskEditResponse(
                    task=PlanQueryService.to_task_response(task),
                    adjustment_id=existing.id,
                    companion_message="这项手动调整已经保存。",
                )

            task, plan = await self._task_and_plan(task_id, user_id, for_update=True)
            self._require_version(task, payload.version)
            patch = self._patch_dict(payload)
            original = self._snapshot(task)
            await self._validate_patch(task, plan, patch)
            self._apply_patch(task, plan, patch)
            adjustment = await self._adjustments.create(
                {
                    "user_id": user_id,
                    "plan_id": plan.id,
                    "task_id": task.id,
                    "status": "applied",
                    "request_text": "用户手动编辑",
                    "original_task_json": original,
                    "proposed_patch_json": patch,
                    "rationale": "用户直接确认的任务细节调整。",
                    "generation_method": "manual",
                    "task_version": payload.version,
                    "idempotency_key": idempotency_key,
                    "applied_at": datetime.now(UTC),
                }
            )
            return TaskEditResponse(
                task=PlanQueryService.to_task_response(task),
                adjustment_id=adjustment.id,
                companion_message="已更新当天任务，周重点和七天边界保持不变。",
            )

    async def propose(
        self,
        *,
        task_id: UUID,
        user_id: UUID,
        payload: TaskAdjustmentCreateRequest,
        idempotency_key: str,
    ) -> TaskAdjustmentProposalResponse:
        async with session_transaction(self._session):
            existing = await self._adjustments.get_by_idempotency(user_id, idempotency_key)
            if existing is not None:
                if existing.task_id != task_id or existing.generation_method == "manual":
                    raise self._idempotency_conflict()
                return self.to_response(existing)
            task, plan = await self._task_and_plan(task_id, user_id)
            self._require_version(task, payload.version)
            editable, reason = self._editability(task, plan)
            if not editable:
                raise AppError(
                    code="STATE_TASK_NOT_EDITABLE",
                    message=reason or "Task cannot be edited",
                    status_code=HTTPStatus.CONFLICT,
                )
            profile = await self._profiles.get_for_user(user_id)
            if profile is None:
                raise AppError(
                    code="STATE_PROFILE_REQUIRED",
                    message="Profile is required before adjusting a Task",
                    status_code=HTTPStatus.CONFLICT,
                )
            focus, signal = self._week_focus(plan)
            task_response = PlanQueryService.to_task_response(task)

        try:
            suggestion = await self._provider.propose(
                task=task_response,
                request_text=payload.message,
                week_focus=focus,
                success_signal=signal,
                daily_budget_minutes=profile.time_budget_minutes,
            )
        except Exception as exc:
            raise AppError(
                code="PROVIDER_TASK_ADJUSTMENT_FAILED",
                message="AI could not produce a valid Task adjustment proposal",
                status_code=HTTPStatus.BAD_GATEWAY,
            ) from exc

        async with session_transaction(self._session):
            task, plan = await self._task_and_plan(task_id, user_id, for_update=True)
            self._require_version(task, payload.version)
            patch = suggestion.patch.model_dump(exclude_none=True)
            await self._validate_patch(task, plan, patch)
            proposal = await self._adjustments.create(
                {
                    "user_id": user_id,
                    "plan_id": plan.id,
                    "task_id": task.id,
                    "status": "pending",
                    "request_text": payload.message,
                    "original_task_json": self._snapshot(task),
                    "proposed_patch_json": patch,
                    "rationale": suggestion.rationale,
                    "generation_method": suggestion.generation_method,
                    "model_id": suggestion.model_id,
                    "task_version": task.version,
                    "idempotency_key": idempotency_key,
                }
            )
            return self.to_response(proposal)

    async def confirm(
        self, *, adjustment_id: UUID, user_id: UUID, version: int
    ) -> TaskEditResponse:
        async with session_transaction(self._session):
            proposal = await self._adjustments.get_for_user(
                adjustment_id, user_id, for_update=True
            )
            if proposal is None:
                raise self._not_found_adjustment()
            if proposal.version != version:
                raise AppError(
                    code="STATE_TASK_ADJUSTMENT_VERSION_CONFLICT",
                    message="Task adjustment version does not match",
                    status_code=HTTPStatus.CONFLICT,
                    details={"current_version": proposal.version},
                )
            if proposal.status != "pending":
                raise AppError(
                    code="STATE_TASK_ADJUSTMENT_NOT_PENDING",
                    message="Task adjustment is no longer pending",
                    status_code=HTTPStatus.CONFLICT,
                )
            task, plan = await self._task_and_plan(
                proposal.task_id, user_id, for_update=True
            )
            self._require_version(task, proposal.task_version)
            patch = dict(proposal.proposed_patch_json)
            await self._validate_patch(task, plan, patch)
            self._apply_patch(task, plan, patch)
            proposal.status = "applied"
            proposal.applied_at = datetime.now(UTC)
            proposal.updated_at = datetime.now(UTC)
            proposal.version += 1
            await self._session.flush()
            return TaskEditResponse(
                task=PlanQueryService.to_task_response(task),
                adjustment_id=proposal.id,
                companion_message="AI 调整方案已确认并应用，本周边界没有改变。",
            )

    async def reject(
        self, *, adjustment_id: UUID, user_id: UUID, version: int
    ) -> TaskAdjustmentProposalResponse:
        async with session_transaction(self._session):
            proposal = await self._adjustments.get_for_user(
                adjustment_id, user_id, for_update=True
            )
            if proposal is None:
                raise self._not_found_adjustment()
            if proposal.version != version or proposal.status != "pending":
                raise AppError(
                    code="STATE_TASK_ADJUSTMENT_NOT_PENDING",
                    message="Task adjustment is no longer pending or has changed",
                    status_code=HTTPStatus.CONFLICT,
                )
            proposal.status = "rejected"
            proposal.rejected_at = datetime.now(UTC)
            proposal.updated_at = datetime.now(UTC)
            proposal.version += 1
            await self._session.flush()
            return self.to_response(proposal)

    async def _task_and_plan(
        self, task_id: UUID, user_id: UUID, *, for_update: bool = False
    ) -> tuple[Task, Plan]:
        task = await self._plans.get_task_for_user(task_id, user_id, for_update=for_update)
        if task is None:
            raise self._not_found_task()
        plan = await self._plans.get_for_user(task.plan_id, user_id, for_update=for_update)
        if plan is None:
            raise AppError(
                code="NOT_FOUND_PLAN",
                message="Plan was not found",
                status_code=HTTPStatus.NOT_FOUND,
            )
        return task, plan

    async def _validate_patch(
        self, task: Task, plan: Plan, patch: dict[str, object]
    ) -> None:
        editable, reason = self._editability(task, plan)
        if not editable:
            raise AppError(
                code="STATE_TASK_NOT_EDITABLE",
                message=reason or "Task cannot be edited",
                status_code=HTTPStatus.CONFLICT,
            )
        validated = TaskEditFields.model_validate(patch)
        profile = await self._profiles.get_for_user(task.user_id)
        if profile is None:
            raise AppError(
                code="STATE_PROFILE_REQUIRED",
                message="Profile is required before editing a Task",
                status_code=HTTPStatus.CONFLICT,
            )
        minutes = validated.estimated_minutes or task.estimated_minutes
        same_day = await self._plans.list_tasks(
            task.user_id,
            scheduled_date=task.scheduled_date,
            plan_id=task.plan_id,
            limit=100,
        )
        daily_total = minutes + sum(
            item.estimated_minutes for item in same_day if item.id != task.id
        )
        if daily_total > profile.time_budget_minutes:
            raise AppError(
                code="VALIDATION_TASK_DAILY_BUDGET",
                message="Edited Task would exceed the daily time budget",
                status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                details={
                    "daily_total": daily_total,
                    "daily_budget": profile.time_budget_minutes,
                },
            )

    @staticmethod
    def _apply_patch(task: Task, plan: Plan, patch: dict[str, object]) -> None:
        if "starter_action" in patch:
            task.completed_step_indexes_json = []
        if "starter_action" in patch or "deliverable" in patch:
            task.deliverable_verified = False
            task.verification_status = "not_ready"
        for field, value in patch.items():
            setattr(task, field, value)
        now = datetime.now(UTC)
        task.version += 1
        task.updated_at = now
        plan.version += 1
        plan.updated_at = now

    @staticmethod
    def _patch_dict(payload: TaskEditRequest) -> dict[str, object]:
        return payload.model_dump(exclude={"version"}, exclude_none=True)

    @staticmethod
    def _snapshot(task: Task) -> dict[str, object]:
        return {
            "title": task.title,
            "starter_action": task.starter_action,
            "deliverable": task.deliverable,
            "rationale": task.rationale,
            "estimated_minutes": task.estimated_minutes,
            "version": task.version,
        }

    @staticmethod
    def _week_focus(plan: Plan) -> tuple[str, str]:
        first = plan.weekly_focus_json[0] if plan.weekly_focus_json else {}
        return (
            str(first.get("focus") or plan.summary),
            str(first.get("success_signal") or plan.summary),
        )

    @staticmethod
    def _editability(task: Task, plan: Plan) -> tuple[bool, str | None]:
        legacy_current_cycle = plan.status == "archived" and fixed_cycle_contains(
            plan_date=plan.plan_date,
            horizon_end=plan.horizon_end,
            target=product_today(),
        )
        if plan.status not in {"generated", "active"} and not legacy_current_cycle:
            return False, "Only the current weekly cycle can be edited"
        if task.state not in {"pending", "in_progress"}:
            return False, "Only an unfinished Task can be edited"
        return True, None

    @staticmethod
    def _require_version(task: Task, version: int) -> None:
        if task.version != version:
            raise AppError(
                code="STATE_TASK_VERSION_CONFLICT",
                message="Task version does not match the current version",
                status_code=HTTPStatus.CONFLICT,
                details={"current_version": task.version},
            )

    @staticmethod
    def to_response(proposal: TaskAdjustmentProposal) -> TaskAdjustmentProposalResponse:
        return TaskAdjustmentProposalResponse(
            adjustment_id=proposal.id,
            plan_id=proposal.plan_id,
            task_id=proposal.task_id,
            status=proposal.status,
            request_text=proposal.request_text,
            original_task=dict(proposal.original_task_json),
            proposed_patch=TaskEditFields.model_validate(proposal.proposed_patch_json),
            rationale=proposal.rationale,
            generation_method=proposal.generation_method,
            model_id=proposal.model_id,
            task_version=proposal.task_version,
            version=proposal.version,
            created_at=proposal.created_at,
        )

    @staticmethod
    def _not_found_task() -> AppError:
        return AppError(
            code="NOT_FOUND_TASK",
            message="Task was not found",
            status_code=HTTPStatus.NOT_FOUND,
        )

    @staticmethod
    def _not_found_adjustment() -> AppError:
        return AppError(
            code="NOT_FOUND_TASK_ADJUSTMENT",
            message="Task adjustment was not found",
            status_code=HTTPStatus.NOT_FOUND,
        )

    @staticmethod
    def _idempotency_conflict() -> AppError:
        return AppError(
            code="STATE_IDEMPOTENCY_KEY_REUSED",
            message="Idempotency-Key was already used for another operation",
            status_code=HTTPStatus.CONFLICT,
        )
