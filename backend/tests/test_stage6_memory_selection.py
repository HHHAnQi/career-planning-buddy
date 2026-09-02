"""Stage 6A memory ranking and context-budget tests."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.agent.context_selection import (
    ScoredMemory,
    build_memory_query,
    combine_memory_score,
    recency_score,
    select_memories,
    select_memories_within_budget,
)
from app.agent.executor import AgentRunExecutor
from app.core.config import Settings
from app.models.agent_run import AgentStep
from app.models.evidence import Memory
from app.models.plan import Plan
from app.providers.embedding import MockEmbeddingProvider
from app.repositories.evidence import EvidenceRepository
from app.repositories.memories import MemoryRepository
from app.schemas.agent_runs import RunInputSnapshot
from tests.test_agent_runtime import create_run, create_user, refresh_run, runtime_factory


def _memory(index: int, *, pinned: bool, similarity: float, summary: str) -> ScoredMemory:
    now = datetime(2026, 8, 4, tzinfo=UTC)
    recency = recency_score(
        last_used_at=None,
        updated_at=now - timedelta(days=index),
        now=now,
    )
    return ScoredMemory(
        memory_id=UUID(int=index),
        version=1,
        memory_type="stable_preference",
        summary=summary,
        pinned=pinned,
        similarity=similarity,
        recency=recency,
        final_score=combine_memory_score(similarity=similarity, recency=recency),
    )


def test_memory_selection_prioritizes_pinned_then_score_and_budget() -> None:
    selected = select_memories_within_budget(
        [
            _memory(1, pinned=False, similarity=0.9, summary="semantic best"),
            _memory(2, pinned=True, similarity=0.1, summary="pinned"),
            _memory(3, pinned=False, similarity=0.7, summary="too long for budget"),
        ],
        max_items=2,
        max_chars=24,
    )

    assert [item.summary for item in selected] == ["pinned", "semantic best"]


def test_memory_query_contains_only_supplied_planning_signals() -> None:
    query = build_memory_query(
        user_message="prepare an Agent project",
        goal_type="agent_app",
        blockers=["not enough time"],
        adjustment_request="reduce daily tasks",
    )

    assert query == ("prepare an Agent project\nagent_app\nnot enough time\nreduce daily tasks")


@pytest.mark.asyncio
async def test_embedding_failure_uses_user_scoped_text_fallback(
    db_session: AsyncSession,
) -> None:
    user_id = await create_user(db_session)
    row = await MemoryRepository(db_session).create_memory(
        user_id=user_id,
        memory_type="execution_pattern",
        summary="Agent 工程需要拆成更小步骤",
        content_json={},
        sensitivity="sensitive",
        embedding=None,
        source_run_id=None,
    )

    result = await select_memories(
        repository=EvidenceRepository(db_session),
        embedding_provider=MockEmbeddingProvider(1024),
        user_id=user_id,
        user_message="[mock:embedding-error] Agent 工程规划",
        goal_type="agent_app",
        blockers=[],
        adjustment_request=None,
        semantic_enabled=True,
        retrieval_limit=8,
        max_items=5,
        max_chars=1200,
        min_similarity=0.35,
        half_life_days=14,
    )

    assert result.fallback_used is True
    assert [item.memory_id for item in result.selected] == [row.id]


@pytest.mark.asyncio
async def test_graph_selects_only_current_users_active_memory_and_updates_snapshot(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    user_id = await create_user(db_session)
    other_user_id = await create_user(db_session)
    embedding = MockEmbeddingProvider(1024)
    repository = MemoryRepository(db_session)
    selected = await repository.create_memory(
        user_id=user_id,
        memory_type="stable_preference",
        summary="Agent 项目优先并减少每日任务",
        content_json={"pinned": True},
        sensitivity="sensitive",
        embedding=(await embedding.embed(["Agent 项目优先并减少每日任务"]))[0],
        source_run_id=None,
    )
    selected_id = selected.id
    selected_version = selected.version
    inactive = await repository.create_memory(
        user_id=user_id,
        memory_type="execution_pattern",
        summary="不应进入上下文",
        content_json={"pinned": True},
        sensitivity="normal",
        embedding=None,
        source_run_id=None,
    )
    inactive_id = inactive.id
    inactive.status = "closed"
    await repository.create_memory(
        user_id=other_user_id,
        memory_type="stable_preference",
        summary="其他用户 Agent 项目偏好",
        content_json={"pinned": True},
        sensitivity="normal",
        embedding=None,
        source_run_id=None,
    )
    settings = Settings(
        _env_file=None,
        app_env="test",
        llm_provider="mock",
        memory_min_similarity=0,
    )
    run = await create_run(
        db_session,
        user_id,
        message="请按 Agent 项目优先并减少每日任务来规划",
        key="stage6-memory-selection-run",
        settings=settings,
    )
    run_id = run.id
    await AgentRunExecutor(
        runtime_factory(db_connection),
        embedding_provider=embedding,
    ).execute(run_id)
    refreshed = await refresh_run(db_session, run_id)
    selected_row = await db_session.get(Memory, selected_id)
    assert selected_row is not None
    await db_session.refresh(selected_row)
    plan = await db_session.scalar(select(Plan).where(Plan.source_run_id == run_id))
    context_step = await db_session.scalar(
        select(AgentStep).where(
            AgentStep.run_id == run_id,
            AgentStep.node_name == "context_builder",
        )
    )

    assert refreshed.status == "completed"
    assert refreshed.input_snapshot_json is not None
    snapshot = RunInputSnapshot.model_validate(refreshed.input_snapshot_json)
    assert snapshot.memory_versions == {str(selected_id): selected_version}
    assert selected_row.last_used_at is not None
    assert plan is not None
    # Catalog visibility does not imply that the model cited the memory.
    assert plan.evidence_refs_json == []
    assert context_step is not None
    assert context_step.trace_data["selected_memory_ids"] == [str(selected_id)]
    assert context_step.trace_data["memory_query_hash"]
    assert str(inactive_id) not in context_step.trace_data["selected_memory_ids"]
