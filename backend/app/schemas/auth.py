"""Authentication and current-user API contracts."""

from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.agent_runs import AgentRunResponse
from app.schemas.base import StrictModel
from app.schemas.goal_briefs import GoalBriefResponse
from app.schemas.plans import ActivePlanResponse, TaskResponse
from app.schemas.profile import ProfileResponse
from app.schemas.reviews import ReviewResponse


class GuestLoginRequest(StrictModel):
    """Optional stable browser device identifier for Guest reuse."""

    device_id: str | None = Field(default=None, min_length=16, max_length=128)


class EmailRegisterRequest(StrictModel):
    """Create a stable email/password account."""

    email: str = Field(
        min_length=3,
        max_length=255,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, min_length=1, max_length=64)


class EmailLoginRequest(StrictModel):
    """Authenticate an existing email/password account."""

    email: str = Field(
        min_length=3,
        max_length=255,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )
    password: str = Field(min_length=8, max_length=128)


class UserSummary(StrictModel):
    """Public user fields safe for client identity restoration."""

    id: UUID
    email: str | None = None
    display_name: str | None
    role: Literal["user", "dev"]


class GuestLoginResponse(StrictModel):
    """Bearer token returned by Guest authentication."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(ge=1)
    user: UserSummary


class AuthTokenResponse(GuestLoginResponse):
    """Bearer token returned by email registration/login."""


class MeResponse(StrictModel):
    """Stage 1 home restoration aggregate."""

    user: UserSummary
    profile_complete: bool
    planning_window_valid: bool
    profile: ProfileResponse | None
    active_plan: ActivePlanResponse | None = None
    today_tasks: list[TaskResponse] = Field(default_factory=list)
    latest_review: ReviewResponse | None = None
    active_run: AgentRunResponse | None = None
    active_goal_brief: GoalBriefResponse | None = None
