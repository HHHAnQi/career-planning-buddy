"""HTTP dependency composition for authentication and services."""

from typing import Annotated, cast

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.eval_executor import EvalRunnerExecutor, eval_runner_executor
from app.agent.executor import AgentRunExecutor, agent_run_executor
from app.core.config import Settings, get_settings
from app.core.database import get_db_session
from app.core.exceptions import AppError
from app.core.security import AuthenticatedUser, TokenService
from app.harness.pairwise_sweep_executor import (
    PairwiseSweepExecutor,
    pairwise_sweep_executor,
)
from app.providers.asr import ASRProvider
from app.providers.embedding import EmbeddingProvider
from app.providers.goal_understanding import GoalUnderstandingProvider
from app.providers.registry import RuntimeProviderRegistry
from app.providers.task_adjustment import TaskAdjustmentProvider
from app.repositories.users import UserRepository
from app.services.agent_runs import AgentRunService
from app.services.auth import AuthService
from app.services.dev import DevTraceService
from app.services.evals import EvalService
from app.services.goal_briefs import GoalBriefService
from app.services.interview_audio import InterviewAudioService
from app.services.interview_coaching import InterviewCoachingService
from app.services.interviews import InterviewService
from app.services.memories import MemoryService
from app.services.plans import PlanQueryService
from app.services.profiles import ProfileService
from app.services.resume_assessments import ResumeAssessmentService
from app.services.resume_documents import ResumeDocumentService
from app.services.resumes import ResumeService
from app.services.reviews import ReviewService
from app.services.task_adjustments import TaskAdjustmentService

bearer_scheme = HTTPBearer(auto_error=False)


def get_embedding_provider(request: Request) -> EmbeddingProvider:
    """Reuse the application-scoped Embedding Provider instead of rebuilding it."""
    providers = cast(RuntimeProviderRegistry, request.app.state.runtime_providers)
    return providers.embedding


def get_goal_understanding_provider(request: Request) -> GoalUnderstandingProvider:
    providers = cast(RuntimeProviderRegistry, request.app.state.runtime_providers)
    return providers.goal_understanding


def get_task_adjustment_provider(request: Request) -> TaskAdjustmentProvider:
    providers = cast(RuntimeProviderRegistry, request.app.state.runtime_providers)
    return providers.task_adjustment


def get_asr_provider(request: Request) -> ASRProvider:
    providers = cast(RuntimeProviderRegistry, request.app.state.runtime_providers)
    return providers.asr


def get_token_service(settings: Annotated[Settings, Depends(get_settings)]) -> TokenService:
    return TokenService(settings)


def get_auth_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    token_service: Annotated[TokenService, Depends(get_token_service)],
) -> AuthService:
    return AuthService(session, token_service)


def get_profile_service(
    session: Annotated[AsyncSession, Depends(get_db_session, use_cache=False)],
) -> ProfileService:
    return ProfileService(session)


def get_agent_run_executor() -> AgentRunExecutor:
    return agent_run_executor


def get_eval_runner_executor() -> EvalRunnerExecutor:
    return eval_runner_executor


def get_pairwise_sweep_executor() -> PairwiseSweepExecutor:
    return pairwise_sweep_executor


def get_agent_run_service(
    session: Annotated[AsyncSession, Depends(get_db_session, use_cache=False)],
    settings: Annotated[Settings, Depends(get_settings)],
    executor: Annotated[AgentRunExecutor, Depends(get_agent_run_executor)],
) -> AgentRunService:
    return AgentRunService(session, settings, executor)


def get_resume_service(
    session: Annotated[AsyncSession, Depends(get_db_session, use_cache=False)],
) -> ResumeService:
    return ResumeService(session)


def get_resume_document_service() -> ResumeDocumentService:
    return ResumeDocumentService()


def get_resume_assessment_service(
    session: Annotated[AsyncSession, Depends(get_db_session, use_cache=False)],
    settings: Annotated[Settings, Depends(get_settings)],
    executor: Annotated[AgentRunExecutor, Depends(get_agent_run_executor)],
) -> ResumeAssessmentService:
    return ResumeAssessmentService(session, settings, executor)


def get_interview_service(
    session: Annotated[AsyncSession, Depends(get_db_session, use_cache=False)],
    settings: Annotated[Settings, Depends(get_settings)],
    executor: Annotated[AgentRunExecutor, Depends(get_agent_run_executor)],
) -> InterviewService:
    return InterviewService(session, settings, executor)


def get_interview_coaching_service(
    session: Annotated[AsyncSession, Depends(get_db_session, use_cache=False)],
    settings: Annotated[Settings, Depends(get_settings)],
    executor: Annotated[AgentRunExecutor, Depends(get_agent_run_executor)],
) -> InterviewCoachingService:
    return InterviewCoachingService(session, settings, executor)


def get_interview_audio_service(
    session: Annotated[AsyncSession, Depends(get_db_session, use_cache=False)],
    settings: Annotated[Settings, Depends(get_settings)],
    provider: Annotated[ASRProvider, Depends(get_asr_provider)],
    executor: Annotated[AgentRunExecutor, Depends(get_agent_run_executor)],
) -> InterviewAudioService:
    return InterviewAudioService(
        session, settings, provider, InterviewService(session, settings, executor)
    )


def get_goal_brief_service(
    session: Annotated[AsyncSession, Depends(get_db_session, use_cache=False)],
    provider: Annotated[GoalUnderstandingProvider, Depends(get_goal_understanding_provider)],
    settings: Annotated[Settings, Depends(get_settings)],
    executor: Annotated[AgentRunExecutor, Depends(get_agent_run_executor)],
) -> GoalBriefService:
    return GoalBriefService(session, provider, AgentRunService(session, settings, executor))


def get_eval_service(
    session: Annotated[AsyncSession, Depends(get_db_session, use_cache=False)],
) -> EvalService:
    return EvalService(session)


def get_plan_query_service(
    session: Annotated[AsyncSession, Depends(get_db_session, use_cache=False)],
) -> PlanQueryService:
    return PlanQueryService(session)


def get_review_service(
    session: Annotated[AsyncSession, Depends(get_db_session, use_cache=False)],
    settings: Annotated[Settings, Depends(get_settings)],
    executor: Annotated[AgentRunExecutor, Depends(get_agent_run_executor)],
) -> ReviewService:
    return ReviewService(session, settings, executor)


def get_task_adjustment_service(
    session: Annotated[AsyncSession, Depends(get_db_session, use_cache=False)],
    provider: Annotated[TaskAdjustmentProvider, Depends(get_task_adjustment_provider)],
) -> TaskAdjustmentService:
    return TaskAdjustmentService(session, provider)


def get_memory_service(
    session: Annotated[AsyncSession, Depends(get_db_session, use_cache=False)],
    embedding_provider: Annotated[EmbeddingProvider, Depends(get_embedding_provider)],
) -> MemoryService:
    return MemoryService(session, embedding_provider)


def get_dev_trace_service(
    session: Annotated[AsyncSession, Depends(get_db_session, use_cache=False)],
    executor: Annotated[AgentRunExecutor, Depends(get_agent_run_executor)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DevTraceService:
    return DevTraceService(session, executor, settings)


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    token_service: Annotated[TokenService, Depends(get_token_service)],
) -> AuthenticatedUser:
    raw_token = (
        credentials.credentials
        if credentials is not None and credentials.scheme.lower() == "bearer"
        else None
    )

    if raw_token is None:
        raise AppError(
            code="AUTH_INVALID_TOKEN",
            message="a valid bearer token is required",
            status_code=401,
        )

    user_id, _token_role = token_service.verify(raw_token)
    user = await UserRepository(session).get_by_id(user_id)
    if user is None or not user.is_active:
        raise AppError(
            code="AUTH_INVALID_TOKEN",
            message="a valid bearer token is required",
            status_code=401,
        )
    return AuthenticatedUser(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
    )


async def require_dev(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> AuthenticatedUser:
    """Restrict developer diagnostics to the persisted dev role."""
    if current_user.role != "dev":
        raise AppError(
            code="AUTH_FORBIDDEN",
            message="developer role is required",
            status_code=403,
        )
    return current_user
