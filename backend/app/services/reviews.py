"""Daily-review, memory feedback, and user-confirmed next-plan use cases."""

import logging
from datetime import UTC, date, datetime, timedelta
from http import HTTPStatus
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.executor import AgentRunExecutor
from app.core.config import Settings
from app.core.database import session_transaction
from app.core.exceptions import AppError
from app.core.time import product_today
from app.harness.events import EventRecorder
from app.harness.snapshots import SnapshotService
from app.models.agent_run import AgentRun
from app.models.review import Review
from app.repositories.agent_runs import AgentRunRepository
from app.repositories.memories import MemoryRepository
from app.repositories.plans import PlanRepository
from app.repositories.profiles import ProfileRepository
from app.repositories.reviews import ReviewRepository
from app.schemas.enums import NextPlanAction, ReplanMode, RunStatus
from app.schemas.reviews import (
    ReviewCreateRequest,
    ReviewListResponse,
    ReviewResponse,
    ReviewUpdateRequest,
    StartNextPlanResponse,
)
from app.services.memory_candidate_distiller import (
    MemoryDistillationInput,
    distill_memory_candidates,
    normalize_candidate_summary,
)

logger = logging.getLogger(__name__)


class ReviewService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        executor: AgentRunExecutor,
    ) -> None:
        self._session = session
        self._settings = settings
        self._executor = executor
        self._reviews = ReviewRepository(session)
        self._plans = PlanRepository(session)
        self._profiles = ProfileRepository(session)
        self._runs = AgentRunRepository(session)
        self._memories = MemoryRepository(session)

    async def create(
        self,
        *,
        user_id: UUID,
        payload: ReviewCreateRequest,
        idempotency_key: str,
    ) -> ReviewResponse:
        try:
            async with session_transaction(self._session):
                existing = await self._reviews.get_by_idempotency(user_id, idempotency_key)
                if existing is not None:
                    return await self._response(existing, user_id)
                plan = await self._plans.get_for_user(payload.plan_id, user_id)
                if plan is None:
                    raise AppError(
                        code="NOT_FOUND_PLAN",
                        message="Plan was not found",
                        status_code=HTTPStatus.NOT_FOUND,
                    )
                if payload.review_date > product_today():
                    raise AppError(
                        code="VALIDATION_REVIEW_DATE_FUTURE",
                        message="review_date cannot be in the future",
                        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                    )
                duplicate = await self._reviews.get_for_plan_date(
                    user_id,
                    payload.plan_id,
                    payload.review_date,
                )
                if duplicate is not None:
                    raise AppError(
                        code="STATE_REVIEW_ALREADY_EXISTS",
                        message="a Review already exists for this Plan and date",
                        status_code=HTTPStatus.CONFLICT,
                    )
                counts = await self._plans.task_state_counts(
                    user_id=user_id,
                    plan_id=payload.plan_id,
                    scheduled_date=payload.review_date,
                )
                recent_reviews = await self._reviews.recent_for_plan(
                    user_id,
                    payload.plan_id,
                    before_date=payload.review_date,
                    limit=1,
                )
                recent_task_states = await self._plans.recent_task_states(user_id, limit=2)
                suggested, reason = self._evaluate_replan(
                    payload,
                    recent_blocker=(recent_reviews[0].blockers if recent_reviews else None),
                    recent_task_states=recent_task_states,
                    abandoned_count=counts.get("abandoned", 0),
                )
                action = NextPlanAction.ADJUST if suggested else NextPlanAction.CONTINUE
                companion = self._companion(
                    completed_count=counts.get("completed", 0),
                    abandoned_count=counts.get("abandoned", 0),
                    action=action,
                )
                review = await self._reviews.create(
                    {
                        "user_id": user_id,
                        "plan_id": payload.plan_id,
                        "review_date": payload.review_date,
                        "mood": payload.mood,
                        "blockers": payload.blockers,
                        "adjustment_request": payload.adjustment_request,
                        "free_text": payload.free_text,
                        "completed_count": counts.get("completed", 0),
                        "abandoned_count": counts.get("abandoned", 0),
                        "suggested_replan": suggested,
                        "replan_reason": reason,
                        "idempotency_key": idempotency_key,
                    }
                )
                await self._plans.create_companion(
                    user_id=user_id,
                    plan_id=payload.plan_id,
                    review_id=review.id,
                    trigger_tag="review_completed",
                    message=companion,
                    template_version="review_completed_v1",
                )
                await self._create_memory_candidates_best_effort(
                    user_id=user_id,
                    review=review,
                    source_run_id=plan.source_run_id,
                    recent_blocker=(recent_reviews[0].blockers if recent_reviews else None),
                )
                return self.to_response(review, companion)
        except IntegrityError as exc:
            raise AppError(
                code="STATE_REVIEW_ALREADY_EXISTS",
                message="a Review already exists for this Plan, date, or idempotency key",
                status_code=HTTPStatus.CONFLICT,
            ) from exc

    async def list_reviews(
        self,
        *,
        user_id: UUID,
        plan_id: UUID | None,
        date_from: date | None,
        date_to: date | None,
        cursor: UUID | None,
        limit: int,
    ) -> ReviewListResponse:
        if date_from is not None and date_to is not None and date_from > date_to:
            raise AppError(
                code="VALIDATION_REVIEW_DATE_RANGE",
                message="date_from must be on or before date_to",
                status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            )
        async with session_transaction(self._session):
            reviews = await self._reviews.list_for_user(
                user_id,
                plan_id=plan_id,
                date_from=date_from,
                date_to=date_to,
                cursor=cursor,
                limit=limit + 1,
            )
            has_more = len(reviews) > limit
            selected = reviews[:limit]
            return ReviewListResponse(
                items=[await self._response(review, user_id) for review in selected],
                next_cursor=selected[-1].id if has_more and selected else None,
            )

    async def get_review(self, *, review_id: UUID, user_id: UUID) -> ReviewResponse:
        async with session_transaction(self._session):
            review = await self._reviews.get_for_user(review_id, user_id)
            if review is None:
                raise AppError(
                    code="NOT_FOUND_REVIEW",
                    message="Review was not found",
                    status_code=HTTPStatus.NOT_FOUND,
                )
            return await self._response(review, user_id)

    async def update_review(
        self, *, review_id: UUID, user_id: UUID, payload: ReviewUpdateRequest
    ) -> ReviewResponse:
        async with session_transaction(self._session):
            review = await self._reviews.get_for_user(review_id, user_id, for_update=True)
            if review is None:
                raise AppError(
                    code="NOT_FOUND_REVIEW",
                    message="Review was not found",
                    status_code=HTTPStatus.NOT_FOUND,
                )
            self._ensure_review_mutable(review)
            if review.version != payload.version:
                raise AppError(
                    code="STATE_REVIEW_VERSION_CONFLICT",
                    message="Review version does not match the current version",
                    status_code=HTTPStatus.CONFLICT,
                    details={"current_version": review.version},
                )
            for field in payload.model_fields_set - {"version"}:
                setattr(review, field, getattr(payload, field))
            review.version += 1
            review.updated_at = datetime.now(UTC)
            counts = await self._plans.task_state_counts(
                user_id=user_id,
                plan_id=review.plan_id,
                scheduled_date=review.review_date,
            )
            recent = await self._reviews.recent_for_plan(
                user_id, review.plan_id, before_date=review.review_date, limit=1
            )
            recent_task_states = await self._plans.recent_task_states(user_id, limit=2)
            evaluation = ReviewCreateRequest(
                plan_id=review.plan_id,
                review_date=review.review_date,
                mood=review.mood,
                blockers=review.blockers,
                adjustment_request=review.adjustment_request,
                free_text=review.free_text,
            )
            review.suggested_replan, review.replan_reason = self._evaluate_replan(
                evaluation,
                recent_blocker=(recent[0].blockers if recent else None),
                recent_task_states=recent_task_states,
                abandoned_count=counts.get("abandoned", 0),
            )
            companion = self._companion(
                completed_count=review.completed_count,
                abandoned_count=review.abandoned_count,
                action=(
                    NextPlanAction.ADJUST
                    if review.suggested_replan or review.adjustment_request
                    else NextPlanAction.CONTINUE
                ),
            )
            companion_row = await self._reviews.companion_for_review(review.id, user_id)
            if companion_row is not None:
                companion_row.message = companion
            await self._memories.delete_pending_candidates_for_review(
                user_id=user_id, review_id=review.id
            )
            await self._create_memory_candidates_best_effort(
                user_id=user_id,
                review=review,
                source_run_id=None,
                recent_blocker=(recent[0].blockers if recent else None),
            )
            await self._session.flush()
            return self.to_response(review, companion)

    async def delete_review(self, *, review_id: UUID, user_id: UUID) -> None:
        async with session_transaction(self._session):
            review = await self._reviews.get_for_user(review_id, user_id, for_update=True)
            if review is None:
                raise AppError(
                    code="NOT_FOUND_REVIEW",
                    message="Review was not found",
                    status_code=HTTPStatus.NOT_FOUND,
                )
            self._ensure_review_mutable(review)
            await self._memories.delete_pending_candidates_for_review(
                user_id=user_id, review_id=review.id
            )
            await self._reviews.delete(review)

    async def start_next_plan(
        self,
        *,
        review_id: UUID,
        user_id: UUID,
        idempotency_key: str,
    ) -> StartNextPlanResponse:
        created = False
        try:
            async with session_transaction(self._session):
                review = await self._reviews.get_for_user(
                    review_id,
                    user_id,
                    for_update=True,
                )
                if review is None:
                    raise AppError(
                        code="NOT_FOUND_REVIEW",
                        message="Review was not found",
                        status_code=HTTPStatus.NOT_FOUND,
                    )
                if review.next_plan_run_id is not None:
                    existing = await self._runs.get_for_user(review.next_plan_run_id, user_id)
                    if existing is None:
                        raise AppError(
                            code="STATE_REVIEW_RUN_MISSING",
                            message="the Review next Plan Run is unavailable",
                            status_code=HTTPStatus.CONFLICT,
                        )
                    return self._start_response(existing)
                idempotent_run = await self._runs.get_by_idempotency(user_id, idempotency_key)
                if idempotent_run is not None:
                    raise AppError(
                        code="STATE_IDEMPOTENCY_KEY_REUSED",
                        message="Idempotency-Key was already used for another Agent Run",
                        status_code=HTTPStatus.CONFLICT,
                    )
                source_plan = await self._plans.get_for_user(review.plan_id, user_id)
                if source_plan is None:
                    raise AppError(
                        code="NOT_FOUND_SOURCE_PLAN",
                        message="source Plan was not found",
                        status_code=HTTPStatus.NOT_FOUND,
                    )
                if source_plan.status == "archived":
                    raise AppError(
                        code="STATE_SOURCE_PLAN_ARCHIVED",
                        message="an archived Plan cannot start another next Plan Run",
                        status_code=HTTPStatus.CONFLICT,
                    )
                cycle_end = min(
                    source_plan.plan_date + timedelta(days=6), source_plan.horizon_end
                )
                if product_today() <= cycle_end:
                    raise AppError(
                        code="STATE_WEEKLY_CYCLE_OPEN",
                        message=(
                            "The fixed weekly cycle is still open; completing Tasks early "
                            "does not roll the schedule forward"
                        ),
                        status_code=HTTPStatus.CONFLICT,
                        details={
                            "cycle_start": source_plan.plan_date.isoformat(),
                            "cycle_end": cycle_end.isoformat(),
                        },
                    )
                profile = await self._profiles.get_for_user(user_id)
                next_cycle_start = source_plan.plan_date + timedelta(days=7)
                if profile is not None and profile.start_date is not None:
                    next_cycle_start = max(next_cycle_start, profile.start_date)
                if (
                    profile is None
                    or profile.start_date is None
                    or profile.deadline is None
                    or next_cycle_start > profile.deadline
                ):
                    raise AppError(
                        code="STATE_GOAL_DEADLINE_REACHED",
                        message=(
                            "The target date has been reached; complete the final review "
                            "instead of generating another weekly cycle"
                        ),
                        status_code=HTTPStatus.CONFLICT,
                    )
                active_run = await self._runs.get_active_for_user(user_id)
                if active_run is not None:
                    raise AppError(
                        code="STATE_RUN_ALREADY_ACTIVE",
                        message="another Agent Run is already active",
                        status_code=HTTPStatus.CONFLICT,
                    )
                mode = (
                    ReplanMode.ADJUST
                    if review.suggested_replan or review.adjustment_request
                    else ReplanMode.CONTINUE
                )
                config = SnapshotService.build_config(self._settings)
                request_text = self._next_plan_request(review, mode)
                run = AgentRun(
                    user_id=user_id,
                    idempotency_key=idempotency_key,
                    request_text=request_text,
                    hint_intent="replan",
                    resolved_intent="replan",
                    replan_mode=mode.value,
                    source_plan_id=review.plan_id,
                    source_review_id=review.id,
                    status="pending",
                    graph_version=config.graph_version,
                    config_snapshot_json=config.model_dump(mode="json"),
                    deadline_at=datetime.now(UTC) + timedelta(seconds=config.deadline_seconds),
                )
                await self._runs.create(run)
                await EventRecorder(self._session).record(
                    run.id,
                    "run.created",
                    {
                        "status": "pending",
                        "graph_version": run.graph_version,
                        "source_review_id": str(review.id),
                    },
                )
                review.next_plan_run_id = run.id
                await self._session.flush()
                created = True
        except IntegrityError as exc:
            raise AppError(
                code="STATE_REVIEW_NEXT_PLAN_CONFLICT",
                message="the Review already has a next Plan Run or another Run is active",
                status_code=HTTPStatus.CONFLICT,
            ) from exc
        if created:
            self._executor.submit(run.id)
        return self._start_response(run)

    async def _response(self, review: Review, user_id: UUID) -> ReviewResponse:
        companion = await self._reviews.companion_for_review(review.id, user_id)
        return self.to_response(review, companion.message if companion else "")

    async def _create_memory_candidates_best_effort(
        self,
        *,
        user_id: UUID,
        review: Review,
        source_run_id: UUID | None,
        recent_blocker: str | None,
    ) -> None:
        try:
            proposals = distill_memory_candidates(
                MemoryDistillationInput(
                    user_id=user_id,
                    source_run_id=source_run_id,
                    review_id=review.id,
                    adjustment_request=review.adjustment_request,
                    blockers=review.blockers,
                    free_text=review.free_text,
                    completed_count=review.completed_count,
                    abandoned_count=review.abandoned_count,
                    recent_blocker=recent_blocker,
                )
            )
            async with self._session.begin_nested():
                for proposal in proposals:
                    normalized = normalize_candidate_summary(proposal.summary)
                    exists = await self._memories.candidate_exists_for_review(
                        user_id=user_id,
                        review_id=review.id,
                        memory_type=proposal.memory_type,
                        normalized_summary=normalized,
                    )
                    if exists:
                        continue
                    content = dict(proposal.content)
                    content["normalized_summary"] = normalized
                    await self._memories.create_candidate(
                        user_id=user_id,
                        memory_type=proposal.memory_type,
                        summary=proposal.summary,
                        content_json=content,
                        sensitivity=proposal.sensitivity,
                        proposed_by_run_id=source_run_id,
                        expires_at=datetime.now(UTC) + timedelta(days=14),
                    )
        except Exception:
            logger.exception(
                "memory_candidate_distillation_failed",
                extra={"review_id": str(review.id), "user_id": str(user_id)},
            )

    @staticmethod
    def to_response(review: Review, companion_message: str) -> ReviewResponse:
        """Build the public Review contract, including its derived fields."""
        action = (
            NextPlanAction.ADJUST
            if review.suggested_replan or review.adjustment_request
            else NextPlanAction.CONTINUE
        )
        return ReviewResponse(
            review_id=review.id,
            plan_id=review.plan_id,
            review_date=review.review_date,
            mood=review.mood,
            blockers=review.blockers,
            adjustment_request=review.adjustment_request,
            free_text=review.free_text,
            completed_count=review.completed_count,
            abandoned_count=review.abandoned_count,
            suggested_replan=review.suggested_replan,
            replan_reason=review.replan_reason,
            next_plan_action=action,
            companion_message=companion_message,
            next_plan_run_id=review.next_plan_run_id,
            version=review.version,
            created_at=review.created_at,
            updated_at=review.updated_at,
        )

    @staticmethod
    def _ensure_review_mutable(review: Review) -> None:
        if review.next_plan_run_id is not None:
            raise AppError(
                code="STATE_REVIEW_ALREADY_CONSUMED",
                message="a Review used by a next Plan Run cannot be changed or deleted",
                status_code=HTTPStatus.CONFLICT,
            )

    @staticmethod
    def _evaluate_replan(
        payload: ReviewCreateRequest,
        *,
        recent_blocker: str | None,
        recent_task_states: list[str],
        abandoned_count: int,
    ) -> tuple[bool, str | None]:
        combined = " ".join(
            text
            for text in (payload.adjustment_request, payload.blockers, payload.free_text)
            if text
        ).lower()
        reasons: list[str] = []
        if payload.adjustment_request:
            reasons.append("用户明确提出调整")
        if abandoned_count >= 2 or recent_task_states[:2] == ["abandoned", "abandoned"]:
            reasons.append("连续任务被放弃")
        if (
            recent_blocker
            and payload.blockers
            and recent_blocker.strip() == payload.blockers.strip()
        ):
            reasons.append("存在持续阻塞")
        if any(
            marker in combined
            for marker in (
                "时间少",
                "少一点",
                "没时间",
                "时间预算",
                "no time",
                "less time",
            )
        ):
            reasons.append("时间预算发生变化")
        if any(
            marker in combined
            for marker in ("换方向", "改变方向", "目标变化", "换岗位", "change goal")
        ):
            reasons.append("目标方向需要确认调整")
        unique_reasons = list(dict.fromkeys(reasons))
        return bool(unique_reasons), "；".join(unique_reasons) or None

    @staticmethod
    def _companion(
        *,
        completed_count: int,
        abandoned_count: int,
        action: NextPlanAction,
    ) -> str:
        if action == NextPlanAction.ADJUST:
            return (
                f"今天完成了 {completed_count} 项、放弃了 {abandoned_count} 项；"
                "这些反馈会用于本周调整，并在周结算时带入下一周。"
            )
        return f"今天完成了 {completed_count} 项；本周继续沿着当前重点推进。"

    @staticmethod
    def _next_plan_request(review: Review, mode: ReplanMode) -> str:
        context = "；".join(
            text for text in (review.adjustment_request, review.blockers, review.free_text) if text
        )
        if not context:
            context = "沿用当前方向，生成下一个固定七天周期"
        return f"基于本周结算生成下一周计划（{mode.value}）：{context}"

    @staticmethod
    def _start_response(run: AgentRun) -> StartNextPlanResponse:
        mode = ReplanMode(run.replan_mode or "continue")
        return StartNextPlanResponse(
            run_id=run.id,
            status=RunStatus(run.status),
            replan_mode=mode,
            events_url=f"/api/v1/agent-runs/{run.id}/events",
        )
