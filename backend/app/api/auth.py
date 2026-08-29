"""Guest authentication and current-user HTTP endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    get_auth_service,
    get_current_user,
    get_profile_service,
)
from app.core.database import get_db_session
from app.core.exceptions import AppError
from app.core.security import AuthenticatedUser
from app.core.time import product_today
from app.repositories.agent_runs import AgentRunRepository
from app.repositories.goal_briefs import GoalBriefRepository
from app.repositories.reviews import ReviewRepository
from app.schemas.agent_runs import AgentRunResponse
from app.schemas.auth import (
    AuthTokenResponse,
    EmailLoginRequest,
    EmailRegisterRequest,
    GuestLoginRequest,
    GuestLoginResponse,
    MeResponse,
    UserSummary,
)
from app.schemas.errors import ErrorResponse
from app.schemas.plans import ActivePlanResponse, TaskResponse
from app.schemas.profile import ProfileResponse
from app.schemas.reviews import ReviewResponse
from app.services.agent_runs import AgentRunService
from app.services.auth import AuthService
from app.services.goal_briefs import GoalBriefService
from app.services.plans import PlanQueryService
from app.services.profiles import ProfileService
from app.services.reviews import ReviewService

router = APIRouter(tags=["auth"])


def _token_response(user: object, access_token: str, expires_in: int) -> AuthTokenResponse:
    return AuthTokenResponse(
        access_token=access_token,
        expires_in=expires_in,
        user=UserSummary.model_validate(user, from_attributes=True),
    )


@router.post(
    "/auth/guest",
    response_model=GuestLoginResponse,
    status_code=201,
    responses={
        200: {"model": GuestLoginResponse, "description": "Existing Guest reused"},
        422: {"model": ErrorResponse, "description": "Invalid request"},
    },
)
async def guest_login(
    payload: GuestLoginRequest,
    response: Response,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> GuestLoginResponse:
    result = await service.login_guest(payload.device_id)
    response.status_code = 201 if result.created else 200
    return GuestLoginResponse(
        access_token=result.access_token,
        expires_in=result.expires_in,
        user=UserSummary.model_validate(result.user, from_attributes=True),
    )


@router.post(
    "/auth/register",
    response_model=AuthTokenResponse,
    status_code=201,
    responses={
        409: {"model": ErrorResponse, "description": "Email already registered"},
        422: {"model": ErrorResponse, "description": "Invalid request"},
    },
)
async def register_email(
    payload: EmailRegisterRequest,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthTokenResponse:
    result = await service.register_email(
        email=str(payload.email),
        password=payload.password,
        display_name=payload.display_name,
    )
    return _token_response(result.user, result.access_token, result.expires_in)


@router.post(
    "/auth/login",
    response_model=AuthTokenResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Invalid credentials"},
        422: {"model": ErrorResponse, "description": "Invalid request"},
    },
)
async def login_email(
    payload: EmailLoginRequest,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthTokenResponse:
    result = await service.login_email(email=str(payload.email), password=payload.password)
    return _token_response(result.user, result.access_token, result.expires_in)


@router.get(
    "/me",
    response_model=MeResponse,
    responses={401: {"model": ErrorResponse, "description": "Invalid token"}},
)
async def get_me(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    profile_service: Annotated[ProfileService, Depends(get_profile_service)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> MeResponse:
    user_id = current_user.id
    profile = await profile_service.get_optional(user_id)

    plan_query = PlanQueryService(session)
    active_plan: ActivePlanResponse | None = None
    today_tasks: list[TaskResponse] = []
    try:
        active_plan = await plan_query.get_active(user_id)
    except AppError as exc:
        if exc.code != "NOT_FOUND_PLAN":
            raise
    if active_plan is not None:
        task_list = await plan_query.list_tasks(
            user_id=user_id,
            scheduled_date=product_today(),
            state=None,
            plan_id=active_plan.plan_id,
            limit=20,
        )
        today_tasks = list(task_list.items)

    active_run: AgentRunResponse | None = None
    run_repo = AgentRunRepository(session)
    active_run_row = await run_repo.get_active_for_user(user_id)
    if active_run_row is not None:
        active_run = AgentRunService.to_response(active_run_row)

    active_goal_brief = None
    active_brief_row = await GoalBriefRepository(session).get_active_for_user(user_id)
    if active_brief_row is not None:
        active_goal_brief = GoalBriefService.to_response(active_brief_row)

    latest_review: ReviewResponse | None = None
    review_repo = ReviewRepository(session)
    latest_rows = await review_repo.list_for_user(
        user_id, plan_id=None, date_from=None, date_to=None, cursor=None, limit=1
    )
    if latest_rows:
        latest_row = latest_rows[0]
        companion = await review_repo.companion_for_review(latest_row.id, user_id)
        latest_review = ReviewService.to_response(
            latest_row,
            companion.message if companion is not None else "",
        )

    profile_complete = profile is not None
    planning_window_valid = (
        profile is not None
        and profile.start_date is not None
        and profile.deadline is not None
        and profile.start_date <= profile.deadline
        and profile.deadline >= product_today()
    )
    return MeResponse(
        user=UserSummary(
            id=current_user.id,
            email=current_user.email,
            display_name=current_user.display_name,
            role="dev" if current_user.role == "dev" else "user",
        ),
        profile_complete=profile_complete,
        planning_window_valid=planning_window_valid,
        profile=(
            ProfileResponse.model_validate(profile, from_attributes=True)
            if profile is not None
            else None
        ),
        active_plan=active_plan,
        today_tasks=today_tasks,
        active_run=active_run,
        active_goal_brief=active_goal_brief,
        latest_review=latest_review,
    )


@router.delete("/me", status_code=204)
async def delete_me(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> None:
    await service.delete_current_user(current_user.id)
