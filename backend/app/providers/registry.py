"""Application-scoped construction of runtime Provider dependencies."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.providers.asr import ASRProvider, build_asr_provider
from app.providers.embedding import EmbeddingProvider, build_embedding_provider
from app.providers.evidence_distillation import (
    EvidenceDistillationProvider,
    build_evidence_distillation_provider,
)
from app.providers.goal_understanding import (
    GoalUnderstandingProvider,
    build_goal_understanding_provider,
)
from app.providers.interview import InterviewProvider, build_interview_provider
from app.providers.llm import PlanningProvider, build_planning_provider
from app.providers.llm_client import LLMClient, build_llm_client
from app.providers.rerank import build_rerank_provider
from app.providers.resume_optimization import (
    ResumeOptimizationProvider,
    build_resume_optimization_provider,
)
from app.providers.search import SearchProvider, build_search_provider
from app.providers.task_adjustment import (
    TaskAdjustmentProvider,
    build_task_adjustment_provider,
)
from app.tools.registry import ToolRegistry, build_tool_registry


@dataclass(frozen=True, slots=True)
class RuntimeProviderRegistry:
    """One coherent Provider graph shared by HTTP services and the Agent executor."""

    planning: PlanningProvider
    interview: InterviewProvider
    resume_optimization: ResumeOptimizationProvider
    asr: ASRProvider
    goal_understanding: GoalUnderstandingProvider
    embedding: EmbeddingProvider
    search: SearchProvider
    evidence_distillation: EvidenceDistillationProvider
    task_adjustment: TaskAdjustmentProvider
    tools: ToolRegistry
    llm_client: LLMClient | None = None

    async def aclose(self) -> None:
        if self.llm_client is not None:
            await self.llm_client.aclose()
        await self.asr.aclose()


def build_runtime_provider_registry(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> RuntimeProviderRegistry:
    """Build each runtime Provider exactly once for one application instance."""
    embedding = build_embedding_provider(settings)
    search = build_search_provider(settings)
    rerank = build_rerank_provider(settings)
    llm_client = build_llm_client(settings) if settings.llm_provider != "mock" else None
    return RuntimeProviderRegistry(
        planning=build_planning_provider(settings, client=llm_client),
        interview=build_interview_provider(settings, llm_client),
        resume_optimization=build_resume_optimization_provider(settings, llm_client),
        asr=build_asr_provider(settings),
        goal_understanding=build_goal_understanding_provider(settings, client=llm_client),
        embedding=embedding,
        search=search,
        evidence_distillation=build_evidence_distillation_provider(settings, client=llm_client),
        task_adjustment=build_task_adjustment_provider(settings, llm_client),
        tools=build_tool_registry(
            settings=settings,
            session_factory=session_factory,
            embedding_provider=embedding,
            search_provider=search,
            rerank_provider=rerank,
        ),
        llm_client=llm_client,
    )
