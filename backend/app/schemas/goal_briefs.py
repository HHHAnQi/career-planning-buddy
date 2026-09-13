"""Goal clarification and explicit confirmation contracts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.agent_runs import AgentRunCreatedResponse
from app.schemas.base import StrictModel
from app.schemas.enums import GoalBriefStatus, ObjectiveType


class GoalExtraction(StrictModel):
    objective_type: ObjectiveType | None = None
    target_role: str | None = Field(default=None, max_length=120)
    objective: str | None = Field(default=None, max_length=500)
    capability_focus: list[str] = Field(default_factory=list, max_length=8)
    tech_stack: list[str] = Field(default_factory=list, max_length=12)
    duration_weeks: int | None = Field(default=None, ge=1, le=8)
    deliverables: list[str] = Field(default_factory=list, max_length=8)
    success_criteria: list[str] = Field(default_factory=list, max_length=8)
    feasibility: Literal["feasible", "tight", "unrealistic"] | None = None
    feasibility_reason: str | None = Field(default=None, max_length=500)
    constrained_strategy: str | None = Field(default=None, max_length=500)


class GoalBriefCreateRequest(StrictModel):
    message: str = Field(min_length=1, max_length=2000)
    hint_intent: Literal["create_plan", "replan"]
    source_plan_id: UUID | None = None


class GoalBriefRefineRequest(StrictModel):
    version: int = Field(ge=1)
    message: str = Field(min_length=1, max_length=2000)


class GoalBriefVersionRequest(StrictModel):
    version: int = Field(ge=1)


class GoalBriefResponse(StrictModel):
    goal_brief_id: UUID
    status: GoalBriefStatus
    source_message: str
    hint_intent: Literal["create_plan", "replan"]
    source_plan_id: UUID | None
    objective_type: ObjectiveType | None
    target_role: str | None
    objective: str | None
    capability_focus: list[str]
    tech_stack: list[str]
    duration_weeks: int | None
    deliverables: list[str]
    success_criteria: list[str]
    assumptions: list[str]
    missing_fields: list[str]
    questions: list[str]
    extraction_method: Literal["rule", "model", "rule_fallback"]
    model_id: str | None
    version: int
    created_at: datetime
    updated_at: datetime


class GoalBriefConfirmResponse(StrictModel):
    goal_brief: GoalBriefResponse
    run: AgentRunCreatedResponse
