"""Stage 6A deterministic Review-to-MemoryCandidate distillation tests."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.agent.executor import AgentRunExecutor
from app.core.config import get_settings
from app.core.time import product_today
from app.models.evidence import MemoryCandidate
from app.repositories.memories import MemoryRepository
from app.schemas.reviews import ReviewCreateRequest
from app.services.memory_candidate_distiller import (
    MemoryDistillationInput,
    distill_memory_candidates,
)
from app.services.reviews import ReviewService
from tests.test_agent_runtime import ManualExecutor, create_user
from tests.test_stage3_review_replan import generated_plan


def test_adjustment_and_repeated_blocker_create_bounded_sensitive_candidates() -> None:
    values = distill_memory_candidates(
        MemoryDistillationInput(
            user_id=UUID("00000000-0000-0000-0000-000000000601"),
            source_run_id=None,
            review_id=UUID("00000000-0000-0000-0000-000000000602"),
            adjustment_request="明天任务少一点",
            blockers="环境配置问题",
            free_text=None,
            completed_count=1,
            abandoned_count=2,
            recent_blocker=" 环境配置问题 ",
        )
    )

    assert [value.memory_type for value in values] == [
        "stable_preference",
        "execution_pattern",
    ]
    assert all(value.sensitivity == "sensitive" for value in values)
    assert all(len(value.summary) <= 120 for value in values)
    assert all(value.content["rule_version"] == "review_memory_v1" for value in values)


def test_successful_review_without_explicit_signal_creates_no_candidate() -> None:
    values = distill_memory_candidates(
        MemoryDistillationInput(
            user_id=UUID("00000000-0000-0000-0000-000000000601"),
            source_run_id=None,
            review_id=UUID("00000000-0000-0000-0000-000000000602"),
            adjustment_request=None,
            blockers=None,
            free_text="今天顺利完成计划",
            completed_count=2,
            abandoned_count=0,
            recent_blocker=None,
        )
    )

    assert values == []


def test_pending_candidate_expires_before_it_can_be_confirmed() -> None:
    candidate = MemoryCandidate(
        user_id=UUID("00000000-0000-0000-0000-000000000601"),
        memory_type="stable_preference",
        summary="expired preference",
        content_json={},
        sensitivity="sensitive",
        status="pending",
        expires_at=datetime.now(UTC),
    )

    MemoryRepository.expire_if_needed(candidate)

    assert candidate.status == "expired"
    assert candidate.decided_at is not None


@pytest.mark.asyncio
async def test_review_transaction_creates_pending_candidates_once(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    user_id = await create_user(db_session)
    _, plan, _ = await generated_plan(
        db_connection,
        db_session,
        user_id,
        key="stage6-candidate-source",
    )
    service = ReviewService(db_session, get_settings(), ManualExecutor())
    payload = ReviewCreateRequest(
        plan_id=plan.id,
        review_date=product_today(),
        mood=3,
        blockers="环境配置问题",
        adjustment_request="明天任务少一点",
    )

    review = await service.create(
        user_id=user_id,
        payload=payload,
        idempotency_key="stage6-candidate-review",
    )
    repeated = await service.create(
        user_id=user_id,
        payload=payload,
        idempotency_key="stage6-candidate-review",
    )
    candidates = list(
        await db_session.scalars(
            select(MemoryCandidate)
            .where(MemoryCandidate.user_id == user_id)
            .order_by(MemoryCandidate.memory_type)
        )
    )

    assert repeated.review_id == review.review_id
    assert len(candidates) == 1
    assert candidates[0].memory_type == "stable_preference"
    assert candidates[0].status == "pending"
    assert candidates[0].content_json["source_review_id"] == str(review.review_id)
    assert candidates[0].expires_at > datetime.now(UTC)


@pytest.mark.asyncio
async def test_distiller_failure_does_not_rollback_review(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = await create_user(db_session)
    _, plan, _ = await generated_plan(
        db_connection,
        db_session,
        user_id,
        key="stage6-candidate-failure-source",
    )

    def fail_distillation(_: MemoryDistillationInput) -> list[object]:
        raise RuntimeError("deterministic distiller unavailable")

    monkeypatch.setattr("app.services.reviews.distill_memory_candidates", fail_distillation)
    response = await ReviewService(db_session, get_settings(), AgentRunExecutor()).create(
        user_id=user_id,
        payload=ReviewCreateRequest(
            plan_id=plan.id,
            review_date=product_today(),
            mood=3,
            adjustment_request="减少任务",
        ),
        idempotency_key="stage6-candidate-failure-review",
    )

    assert response.review_id is not None
    assert (
        list(
            await db_session.scalars(
                select(MemoryCandidate).where(MemoryCandidate.user_id == user_id)
            )
        )
        == []
    )
