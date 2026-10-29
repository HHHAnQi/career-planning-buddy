"""Read-only application service that supplies planning graph context facts."""

from datetime import timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.context_selection import MemorySelectionResult, select_memories
from app.agent.errors import StructuredOutputError
from app.agent.ports import MemoryQueryFacts
from app.core.database import session_transaction
from app.core.time import product_today
from app.models.plan import Plan
from app.models.review import Review
from app.models.user_profile import UserProfile
from app.providers.embedding import EmbeddingProvider
from app.repositories.evidence import EvidenceRepository
from app.repositories.interviews import InterviewRepository
from app.repositories.plans import PlanRepository
from app.repositories.reviews import ReviewRepository
from app.schemas.agent_runs import (
    HistoryParcel,
    PlanContext,
    PlanFocusContext,
    ProfileContext,
    ReviewContext,
    TaskContext,
)
from app.schemas.enums import PlanStatus, TaskStatus


class PlanningContextService:
    """Own context persistence queries and ORM-to-DTO mapping."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        embedding_provider: EmbeddingProvider,
    ) -> None:
        self._session_factory = session_factory
        self._embedding_provider = embedding_provider

    async def select_memories(
        self,
        *,
        user_id: UUID,
        user_message: str,
        goal_type: str,
        blockers: list[str],
        adjustment_request: str | None,
        semantic_enabled: bool,
        retrieval_limit: int,
        max_items: int,
        max_chars: int,
        min_similarity: float,
        half_life_days: int,
        exclude_categories: set[str] | None,
        precomputed_vector: list[float] | None,
    ) -> MemorySelectionResult:
        async with self._session_factory() as session:
            async with session_transaction(session):
                return await select_memories(
                    repository=EvidenceRepository(session),
                    embedding_provider=self._embedding_provider,
                    user_id=user_id,
                    user_message=user_message,
                    goal_type=goal_type,
                    blockers=blockers,
                    adjustment_request=adjustment_request,
                    semantic_enabled=semantic_enabled,
                    retrieval_limit=retrieval_limit,
                    max_items=max_items,
                    max_chars=max_chars,
                    min_similarity=min_similarity,
                    half_life_days=half_life_days,
                    exclude_categories=exclude_categories,
                    precomputed_vector=precomputed_vector,
                )

    async def load_memory_query_facts(
        self,
        *,
        user_id: UUID,
        source_review_id: UUID | None,
    ) -> MemoryQueryFacts:
        async with self._session_factory() as session:
            async with session_transaction(session):
                plans = PlanRepository(session)
                reviews = ReviewRepository(session)
                source_review = (
                    await reviews.get_for_user(source_review_id, user_id)
                    if source_review_id is not None
                    else None
                )
                task_rows = await plans.recent_tasks(user_id, limit=30)
                blockers = [
                    task.abandoned_reason_text or task.deliverable
                    for task in task_rows
                    if task.state == "abandoned"
                ][:10]
                if source_review is not None and source_review.blockers:
                    blockers.insert(0, source_review.blockers)
                    blockers = list(dict.fromkeys(blockers))[:10]
                return MemoryQueryFacts(
                    blockers=blockers,
                    adjustment_request=(
                        source_review.adjustment_request if source_review else None
                    ),
                )

    async def load_history(
        self,
        *,
        user_id: UUID,
        source_plan_id: UUID | None,
        source_review_id: UUID | None,
        source_interview_report_session_id: UUID | None,
        request_message: str,
    ) -> HistoryParcel:
        async with self._session_factory() as session:
            async with session_transaction(session):
                plans = PlanRepository(session)
                reviews = ReviewRepository(session)
                source_plan = (
                    await plans.get_for_user(source_plan_id, user_id)
                    if source_plan_id is not None
                    else None
                )
                source_review = (
                    await reviews.get_for_user(source_review_id, user_id)
                    if source_review_id is not None
                    else None
                )
                if source_review_id is not None and (
                    source_review is None
                    or source_plan is None
                    or source_review.plan_id != source_plan.id
                ):
                    raise StructuredOutputError(
                        "source Review and source Plan must belong to the Run user"
                    )

                task_rows = await plans.recent_tasks(user_id, limit=30)
                recent_tasks = [
                    TaskContext(
                        task_id=task.id,
                        state=TaskStatus(task.state),
                        title=task.title,
                        deliverable=task.deliverable,
                        scheduled_date=task.scheduled_date,
                        abandoned_reason=task.abandoned_reason,
                        abandoned_reason_text=task.abandoned_reason_text,
                    )
                    for task in task_rows
                ]
                completed_facts = [
                    task.deliverable for task in task_rows if task.state == "completed"
                ][:20]
                blockers = [
                    task.abandoned_reason_text or task.deliverable
                    for task in task_rows
                    if task.state == "abandoned"
                ][:10]
                recent_reviews: list[ReviewContext] = []
                if source_plan is not None:
                    recent_reviews = [
                        self._review_context(item)
                        for item in await reviews.recent_for_plan(
                            user_id,
                            source_plan.id,
                            limit=7,
                        )
                    ]
                if source_review is not None and source_review.blockers:
                    blockers.insert(0, source_review.blockers)
                    blockers = list(dict.fromkeys(blockers))[:10]

                interview_actions = await self._load_interview_actions(
                    session=session,
                    user_id=user_id,
                    report_session_id=source_interview_report_session_id,
                    request_message=request_message,
                )

        planning_date = None
        if source_plan is not None:
            planning_date = max(product_today(), source_plan.plan_date + timedelta(days=7))
        return HistoryParcel(
            source_plan=self._plan_context(source_plan) if source_plan else None,
            source_review=self._review_context(source_review) if source_review else None,
            source_plan_id=source_plan.id if source_plan else None,
            source_plan_version=source_plan.version if source_plan else None,
            planning_date=planning_date,
            recent_tasks=recent_tasks,
            recent_reviews=recent_reviews,
            completed_facts=completed_facts,
            blockers=blockers,
            interview_training_actions=interview_actions,
        )

    @staticmethod
    async def _load_interview_actions(
        *,
        session: AsyncSession,
        user_id: UUID,
        report_session_id: UUID | None,
        request_message: str,
    ) -> list[str]:
        if report_session_id is None:
            return []
        report_session = await InterviewRepository(session).get_session(
            report_session_id,
            user_id,
        )
        if report_session is None or report_session.report_json is None:
            raise StructuredOutputError(
                "source Interview Report must belong to the Run user"
            )
        raw_actions = report_session.report_json.get("recommended_training_actions", [])
        actions = raw_actions if isinstance(raw_actions, list) else []
        return [
            str(item.get("title")) + "：" + str(item.get("deliverable"))
            for item in actions
            if (
                isinstance(item, dict)
                and item.get("title")
                and item.get("deliverable")
                and str(item.get("title")) in request_message
            )
        ][:3]

    @staticmethod
    def _plan_context(plan: Plan) -> PlanContext:
        return PlanContext(
            plan_id=plan.id,
            version=plan.version,
            status=PlanStatus(plan.status),
            plan_date=plan.plan_date,
            horizon_start=plan.horizon_start,
            horizon_end=plan.horizon_end,
            overall_direction=plan.overall_direction,
            weekly_focus=[
                PlanFocusContext.model_validate(item) for item in plan.weekly_focus_json
            ],
        )

    @staticmethod
    def _review_context(review: Review) -> ReviewContext:
        return ReviewContext(
            review_id=review.id,
            review_date=review.review_date,
            blockers=review.blockers,
            adjustment_request=review.adjustment_request,
            free_text=review.free_text,
            replan_reason=review.replan_reason,
        )
    async def load_profile(self, *, user_id: UUID) -> ProfileContext | None:
        async with self._session_factory() as session:
            async with session_transaction(session):
                profile = await session.get(UserProfile, user_id)
                if profile is None:
                    return None
                return ProfileContext.model_validate(profile, from_attributes=True)
