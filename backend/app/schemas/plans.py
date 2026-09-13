"""Plan and Task resource response contracts."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.agent_runs import WeeklyFocusCandidate
from app.schemas.base import StrictModel
from app.schemas.enums import AbandonedReason, PlanStatus, TaskStatus, TaskType


class TaskUpdateRequest(StrictModel):
    state: Literal[
        TaskStatus.IN_PROGRESS,
        TaskStatus.COMPLETED,
        TaskStatus.ABANDONED,
    ]
    version: int = Field(ge=1)
    actual_minutes: int | None = Field(default=None, ge=0, le=1440)
    abandoned_reason: AbandonedReason | None = None
    abandoned_reason_text: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_state_fields(self) -> "TaskUpdateRequest":
        if self.state == TaskStatus.COMPLETED:
            if self.actual_minutes is None:
                raise ValueError("actual_minutes is required when completing a task")
            if self.abandoned_reason is not None or self.abandoned_reason_text is not None:
                raise ValueError("abandonment fields are not allowed for completed tasks")
        elif self.state == TaskStatus.ABANDONED:
            if self.abandoned_reason is None:
                raise ValueError("abandoned_reason is required when abandoning a task")
            if (
                self.abandoned_reason == AbandonedReason.OTHER
                and self.abandoned_reason_text is None
            ):
                raise ValueError("abandoned_reason_text is required for reason other")
            if (
                self.abandoned_reason != AbandonedReason.OTHER
                and self.abandoned_reason_text is not None
            ):
                raise ValueError("abandoned_reason_text is only allowed for reason other")
            if self.actual_minutes is not None:
                raise ValueError("actual_minutes is only allowed for completed tasks")
        elif (
            self.actual_minutes is not None
            or self.abandoned_reason is not None
            or self.abandoned_reason_text is not None
        ):
            raise ValueError("completion and abandonment fields do not apply to this state")
        return self


class TaskChecklistUpdateRequest(StrictModel):
    version: int = Field(ge=1)
    step_index: int = Field(ge=0)
    step_completed: bool


class TaskVerificationRequest(StrictModel):
    version: int = Field(ge=1)
    passed: bool
    actual_minutes: int | None = Field(default=None, ge=1, le=1440)

    @model_validator(mode="after")
    def validate_verification_result(self) -> "TaskVerificationRequest":
        if self.passed and self.actual_minutes is None:
            raise ValueError("actual_minutes is required when verification passes")
        if not self.passed and self.actual_minutes is not None:
            raise ValueError("actual_minutes is only recorded after verification passes")
        return self


class TaskExecutionStep(StrictModel):
    index: int = Field(ge=0)
    text: str
    completed: bool


class TaskResponse(StrictModel):
    task_id: UUID
    plan_id: UUID
    title: str
    task_type: TaskType
    scheduled_date: date
    order_index: int = Field(ge=0)
    state: TaskStatus
    starter_action: str
    execution_steps: list[TaskExecutionStep]
    deliverable: str
    deliverable_verified: bool
    verification_status: Literal["not_ready", "ready", "failed", "passed"]
    completion_ready: bool
    rationale: str | None
    estimated_minutes: int
    actual_minutes: int | None
    abandoned_reason: AbandonedReason | None
    abandoned_reason_text: str | None
    version: int
    started_at: datetime | None
    completed_at: datetime | None
    abandoned_at: datetime | None
    created_at: datetime


class TaskListResponse(StrictModel):
    items: list[TaskResponse]


class TaskUpdateResponse(StrictModel):
    task: TaskResponse
    plan_status: PlanStatus
    companion_message: str


class TaskEditFields(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    starter_action: str | None = Field(default=None, min_length=1, max_length=240)
    deliverable: str | None = Field(default=None, min_length=1, max_length=240)
    rationale: str | None = Field(default=None, min_length=1, max_length=500)
    estimated_minutes: int | None = Field(default=None, ge=5, le=480)

    @model_validator(mode="after")
    def require_change(self) -> "TaskEditFields":
        editable = {"title", "starter_action", "deliverable", "rationale", "estimated_minutes"}
        if not self.model_fields_set.intersection(editable):
            raise ValueError("at least one editable field is required")
        return self


class TaskEditRequest(TaskEditFields):
    version: int = Field(ge=1)


class TaskEditResponse(StrictModel):
    task: TaskResponse
    adjustment_id: UUID
    companion_message: str


class TaskAdjustmentCreateRequest(StrictModel):
    version: int = Field(ge=1)
    message: str = Field(min_length=1, max_length=1000)


class TaskAdjustmentDecisionRequest(StrictModel):
    version: int = Field(ge=1)


class TaskAdjustmentProposalResponse(StrictModel):
    adjustment_id: UUID
    plan_id: UUID
    task_id: UUID
    status: Literal["pending", "applied", "rejected"]
    request_text: str
    original_task: dict[str, object]
    proposed_patch: TaskEditFields
    rationale: str
    generation_method: Literal["manual", "rule", "model", "rule_fallback"]
    model_id: str | None
    task_version: int
    version: int
    created_at: datetime


class TaskDetailResponse(StrictModel):
    task: TaskResponse
    week_focus: str
    week_success_signal: str
    editable: bool
    edit_reason: str | None


class PlanSourceResponse(StrictModel):
    kind: Literal["memory", "experience_atom", "search_source"]
    id: UUID
    available: bool
    title: str | None = None
    url: str | None = None
    snippet: str | None = None
    reliability: float | None = Field(default=None, ge=0, le=1)


class PlanSourcesResponse(StrictModel):
    items: list[PlanSourceResponse]


class ActivePlanResponse(StrictModel):
    plan_id: UUID
    status: PlanStatus
    plan_date: date
    horizon_start: date
    horizon_end: date
    overall_direction: str
    weekly_focus: list[WeeklyFocusCandidate]
    summary: str
    rationale: str
    adjustment_reason: str | None
    sources: list[PlanSourceResponse] = Field(default_factory=list)
    tasks: list[TaskResponse]
    companion_message: str | None
    version: int
    adopted_at: datetime | None
    created_at: datetime


class PlanListResponse(StrictModel):
    items: list[ActivePlanResponse]
    next_cursor: UUID | None
