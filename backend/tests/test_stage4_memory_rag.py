"""Stage 4 Memory, pgvector RAG, Search Mock, and Tool Calling acceptance tests."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.agent.errors import ProviderConfigurationError
from app.agent.executor import AgentRunExecutor
from app.agent.nodes import validate_candidate
from app.core.config import Settings
from app.harness.evidence import build_evidence_visibility
from app.models.agent_run import AgentEvent, AgentStep, ToolCall
from app.models.evidence import ExperienceAtom, Memory, SearchSource
from app.models.plan import Plan
from app.providers.embedding import (
    LocalEmbeddingProvider,
    MockEmbeddingProvider,
)
from app.providers.llm import MockPlanningProvider
from app.providers.search import MockSearchProvider
from app.repositories.memories import MemoryRepository
from app.schemas.agent_runs import EvidenceCatalogItem
from app.schemas.enums import GoalType
from app.services.memories import MemoryService
from app.services.plans import PlanQueryService
from app.tools.contracts import ToolContext
from app.tools.registry import build_tool_registry
from tests.test_agent_nodes import candidate
from tests.test_agent_runtime import (
    create_run,
    create_user,
    refresh_run,
    runtime_factory,
)
from tests.test_profile_api import bearer, guest_login


def stage4_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_url=(
            "postgresql+asyncpg://career_buddy:career_buddy_local@127.0.0.1:5432/career_buddy"
        ),
        jwt_secret="test-secret-with-at-least-32-characters",
        llm_provider="mock",
        embedding_provider="mock",
        embedding_dim=1024,
        agent_deadline_seconds=45,
    )


@pytest.mark.asyncio
async def test_mock_embedding_is_deterministic_and_pgvector_retrieval_is_scoped(
    db_session: AsyncSession,
) -> None:
    embedding = MockEmbeddingProvider(1024)
    first, repeated, other = await embedding.embed(["Agent 评测", "Agent 评测", "前端设计"])
    assert first == repeated
    assert len(first) == 1024
    assert first != other

    atom = ExperienceAtom(
        goal_type="agent_app",
        title="Agent 评测闭环",
        content="为固定 Agent Graph 建立回放数据与确定性指标。",
        evidence_json={"source": "curated-fixture", "reliability": 0.9},
        embedding=first,
    )
    wrong_goal = ExperienceAtom(
        goal_type="frontend",
        title="前端组件",
        content="前端组件测试经验。",
        evidence_json={"source": "curated-fixture", "reliability": 0.8},
        embedding=first,
    )
    db_session.add_all([atom, wrong_goal])
    await db_session.flush()

    from app.repositories.evidence import EvidenceRepository

    results = await EvidenceRepository(db_session).rag_retrieve(
        goal_type="agent_app", vector=first, limit=5
    )
    result_ids = [row.id for row, _score in results]
    assert results[0][0].id == atom.id
    assert atom.id in result_ids
    assert wrong_goal.id not in result_ids
    assert results[0][1] == pytest.approx(1.0, abs=1e-5)


@pytest.mark.asyncio
async def test_local_embedding_never_downloads_missing_weights() -> None:
    model_path = Path("tests/.missing-bge-model")
    provider = LocalEmbeddingProvider(
        model_path,
        dimension=1024,
        model_name="missing-local-bge-fixture",
    )
    with pytest.raises(ProviderConfigurationError):
        await provider.embed(["本地模型检查"])


@pytest.mark.asyncio
async def test_memory_api_consent_lifecycle_optimistic_lock_and_user_isolation(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    token, user_id_raw, _ = await guest_login(api_client)
    other_token, other_user_id_raw, _ = await guest_login(api_client)
    user_id = UUID(user_id_raw)
    other_user_id = UUID(other_user_id_raw)
    repository = MemoryRepository(db_session)
    normal = await repository.create_memory(
        user_id=user_id,
        memory_type="stable_preference",
        summary="偏好先完成可运行的小增量",
        content_json={"pinned": True},
        sensitivity="normal",
        embedding=None,
        source_run_id=None,
    )
    await repository.create_memory(
        user_id=user_id,
        memory_type="profile_fact",
        summary="敏感测试记忆",
        content_json={},
        sensitivity="sensitive",
        embedding=None,
        source_run_id=None,
    )
    other_memory = await repository.create_memory(
        user_id=other_user_id,
        memory_type="profile_fact",
        summary="其他用户记忆",
        content_json={},
        sensitivity="normal",
        embedding=None,
        source_run_id=None,
    )
    candidate_row = await repository.create_candidate(
        user_id=user_id,
        memory_type="execution_pattern",
        summary="遇到阻碍时需要更小步骤",
        content_json={"signal": "blocked"},
        sensitivity="sensitive",
        proposed_by_run_id=None,
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    rejected_candidate = await repository.create_candidate(
        user_id=user_id,
        memory_type="profile_fact",
        summary="不应激活的敏感候选",
        content_json={},
        sensitivity="highly_sensitive",
        proposed_by_run_id=None,
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )

    listed = await api_client.get("/api/v1/memories", headers=bearer(token))
    assert listed.status_code == 200
    assert [item["memory_id"] for item in listed.json()["items"]] == [str(normal.id)]

    closed = await api_client.patch(
        f"/api/v1/memories/{normal.id}",
        headers=bearer(token),
        json={"status": "closed", "version": 1},
    )
    assert closed.status_code == 200
    assert closed.json()["version"] == 2
    stale = await api_client.patch(
        f"/api/v1/memories/{normal.id}",
        headers=bearer(token),
        json={"status": "active", "version": 1},
    )
    assert stale.status_code == 409
    restored = await api_client.patch(
        f"/api/v1/memories/{normal.id}",
        headers=bearer(token),
        json={"status": "active", "version": 2},
    )
    assert restored.status_code == 200
    assert restored.json()["version"] == 3
    isolated = await api_client.delete(f"/api/v1/memories/{other_memory.id}", headers=bearer(token))
    assert isolated.status_code == 404
    assert (
        await api_client.get("/api/v1/memories", headers=bearer(other_token))
    ).status_code == 200

    confirm_url = f"/api/v1/memory-candidates/{candidate_row.id}/confirm"
    confirmed = await api_client.post(
        confirm_url,
        headers={**bearer(token), "Idempotency-Key": "confirm-memory"},
    )
    assert confirmed.status_code == 200
    activated_id = confirmed.json()["memory"]["memory_id"]
    repeated = await api_client.post(
        confirm_url,
        headers={**bearer(token), "Idempotency-Key": "confirm-memory"},
    )
    assert repeated.status_code == 200
    assert repeated.json()["memory"]["memory_id"] == activated_id
    changed_key = await api_client.post(
        confirm_url,
        headers={**bearer(token), "Idempotency-Key": "confirm-memory-other"},
    )
    assert changed_key.status_code == 409
    assert changed_key.json()["error"]["code"] == "STATE_IDEMPOTENCY_KEY_REUSED"
    rejected = await api_client.post(
        f"/api/v1/memory-candidates/{rejected_candidate.id}/reject",
        headers={**bearer(token), "Idempotency-Key": "reject-memory"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["candidate"]["status"] == "rejected"
    repeated_reject = await api_client.post(
        f"/api/v1/memory-candidates/{rejected_candidate.id}/reject",
        headers={**bearer(token), "Idempotency-Key": "reject-memory"},
    )
    assert repeated_reject.status_code == 200
    reused_for_other_request = await api_client.post(
        confirm_url,
        headers={**bearer(token), "Idempotency-Key": "reject-memory"},
    )
    assert reused_for_other_request.status_code == 409
    assert (
        reused_for_other_request.json()["error"]["code"]
        == "STATE_IDEMPOTENCY_KEY_REUSED"
    )
    deleted = await api_client.delete(f"/api/v1/memories/{normal.id}", headers=bearer(token))
    assert deleted.status_code == 204


@pytest.mark.asyncio
async def test_search_tool_deduplicates_persists_and_reuses_fixture(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    user_id = await create_user(db_session)
    run = await create_run(db_session, user_id, key="stage4-search-tool")
    run.status = "running"
    run.worker_id = "test-worker"
    run.lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
    step = AgentStep(
        run_id=run.id,
        sequence=1,
        node_name="career_planning_agent",
        status="running",
        trace_data={},
    )
    db_session.add(step)
    await db_session.flush()
    sessions = runtime_factory(db_connection)
    registry = build_tool_registry(
        settings=stage4_settings(),
        session_factory=sessions,
        embedding_provider=MockEmbeddingProvider(1024),
        search_provider=MockSearchProvider(),
    )
    context = ToolContext(
        run_id=run.id,
        user_id=user_id,
        goal_type=GoalType.AGENT_APP,
        requires_fresh_information=True,
        remaining_deadline_ms=8000,
    )
    arguments: dict[str, object] = {"query": "最新 Agent 岗位", "limit": 5}
    first = await registry.execute(
        tool_name="web_search",
        arguments=arguments,
        context=context,
        step_id=step.id,
        round_number=1,
    )
    repeated = await registry.execute(
        tool_name="web_search",
        arguments=arguments,
        context=context,
        step_id=step.id,
        round_number=1,
    )
    assert first.success is True
    assert repeated.reused is True
    assert len(first.result.evidence) == 2
    assert (
        await db_session.scalar(
            select(func.count()).select_from(SearchSource).where(SearchSource.run_id == run.id)
        )
        == 2
    )
    assert (
        await db_session.scalar(
            select(func.count()).select_from(ToolCall).where(ToolCall.run_id == run.id)
        )
        == 1
    )


@pytest.mark.asyncio
async def test_tool_argument_timeout_and_availability_guards(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    user_id = await create_user(db_session)
    run = await create_run(db_session, user_id, key="stage4-tool-guards")
    run.status = "running"
    run.worker_id = "test-worker"
    run.lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
    step = AgentStep(
        run_id=run.id,
        sequence=1,
        node_name="career_planning_agent",
        status="running",
        trace_data={},
    )
    db_session.add(step)
    await db_session.flush()
    settings = stage4_settings().model_copy(update={"tool_timeout_seconds": 0.01})
    registry = build_tool_registry(
        settings=settings,
        session_factory=runtime_factory(db_connection),
        embedding_provider=MockEmbeddingProvider(1024),
        search_provider=MockSearchProvider(),
    )
    assert [item.name for item in registry.available_specs()] == [
        "memory_lookup",
        "rag_retrieve",
        "document_search",
    ]
    assert [item.name for item in registry.available_specs(requires_fresh_information=True)] == [
        "memory_lookup",
        "rag_retrieve",
        "document_search",
        "web_search",
    ]
    context = ToolContext(
        run_id=run.id,
        user_id=user_id,
        goal_type=GoalType.AGENT_APP,
        remaining_deadline_ms=5000,
    )
    blocked_search = await registry.execute(
        tool_name="web_search",
        arguments={"query": "latest", "limit": 1},
        context=context,
        step_id=step.id,
        round_number=1,
    )
    invalid = await registry.execute(
        tool_name="memory_lookup",
        arguments={"query": "x", "limit": 6},
        context=context,
        step_id=step.id,
        round_number=1,
    )
    fresh_context = context.model_copy(update={"requires_fresh_information": True})
    timed_out = await registry.execute(
        tool_name="web_search",
        arguments={"query": "[mock:search-timeout]", "limit": 1},
        context=fresh_context,
        step_id=step.id,
        round_number=2,
    )
    unknown = await registry.execute(
        tool_name="unregistered_tool",
        arguments={"query": "x"},
        context=context,
        step_id=step.id,
        round_number=2,
    )
    over_budget = await registry.execute(
        tool_name="rag_retrieve",
        arguments={"query": "x", "goal_type": "agent_app", "limit": 1},
        context=context,
        step_id=step.id,
        round_number=2,
    )
    assert blocked_search.error_code == "TOOL_NOT_ALLOWED"
    assert invalid.error_code == "TOOL_ARGUMENT_INVALID"
    assert timed_out.error_code == "TOOL_TIMEOUT"
    assert unknown.error_code == "TOOL_NOT_ALLOWED"
    assert over_budget.error_code == "TOOL_BUDGET_EXCEEDED"


@pytest.mark.asyncio
async def test_agent_tool_loop_persists_evidence_and_keeps_terminal_last(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    user_id = await create_user(db_session)
    embedding = MockEmbeddingProvider(1024)
    vector = (await embedding.embed(["Agent 工程求职证据"]))[0]
    memory = Memory(
        user_id=user_id,
        memory_type="profile_fact",
        summary="Agent 工程求职证据",
        content_json={"pinned": False},
        sensitivity="normal",
        status="active",
        embedding=vector,
    )
    db_session.add(memory)
    await db_session.flush()
    memory_id = memory.id
    settings = stage4_settings().model_copy(update={"agent_max_tool_rounds": 1})
    sessions = runtime_factory(db_connection)
    registry = build_tool_registry(
        settings=settings,
        session_factory=sessions,
        embedding_provider=embedding,
        search_provider=MockSearchProvider(),
    )
    run = await create_run(
        db_session,
        user_id,
        key="stage4-agent-tool-loop",
        message="[mock:tool-memory] 帮我制定四周求职计划",
        settings=settings,
    )
    await AgentRunExecutor(
        sessions,
        MockPlanningProvider(),
        registry,
    ).execute(run.id)
    completed = await refresh_run(db_session, run.id)
    plan = await db_session.scalar(select(Plan).where(Plan.source_run_id == run.id))
    events = list(
        await db_session.scalars(
            select(AgentEvent).where(AgentEvent.run_id == run.id).order_by(AgentEvent.sequence)
        )
    )
    agent_step = await db_session.scalar(
        select(AgentStep).where(
            AgentStep.run_id == run.id,
            AgentStep.node_name == "career_planning_agent",
        )
    )
    assert completed.status == "completed"
    assert plan is not None
    assert plan.evidence_refs_json == [{"kind": "memory", "id": str(memory_id)}]
    assert agent_step is not None
    assert agent_step.trace_data["visible_evidence_ids"] == [str(memory_id)]
    assert agent_step.trace_data["visible_evidence_count"] == 1
    assert [event.event_type for event in events if event.event_type.startswith("tool.")] == [
        "tool.called",
        "tool.returned",
    ]
    assert events[-1].event_type == "run.completed"
    sources = await PlanQueryService(db_session).get_sources(plan.id, user_id)
    assert len(sources.items) == 1
    assert sources.items[0].kind == "memory"
    assert sources.items[0].available is True


@pytest.mark.asyncio
async def test_search_provider_failure_degrades_tool_but_not_plan(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    user_id = await create_user(db_session)
    settings = stage4_settings()
    sessions = runtime_factory(db_connection)
    registry = build_tool_registry(
        settings=settings,
        session_factory=sessions,
        embedding_provider=MockEmbeddingProvider(1024),
        search_provider=MockSearchProvider(),
    )
    run = await create_run(
        db_session,
        user_id,
        key="stage4-search-failure",
        message=("[mock:tool-search] [mock:search-error] 搜索最新岗位后制定四周计划"),
        settings=settings,
    )
    await AgentRunExecutor(sessions, MockPlanningProvider(), registry).execute(run.id)
    completed = await refresh_run(db_session, run.id)
    plan = await db_session.scalar(select(Plan).where(Plan.source_run_id == run.id))
    failures = list(
        await db_session.scalars(
            select(ToolCall).where(ToolCall.run_id == run.id, ToolCall.success.is_(False))
        )
    )
    assert completed.status == "completed"
    assert plan is not None
    assert plan.evidence_refs_json == []
    assert failures
    assert {item.error_code for item in failures} == {"TOOL_PROVIDER_UNAVAILABLE"}


@pytest.mark.asyncio
async def test_embedding_failure_degrades_rag_but_not_plan(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    user_id = await create_user(db_session)
    settings = stage4_settings()
    sessions = runtime_factory(db_connection)
    registry = build_tool_registry(
        settings=settings,
        session_factory=sessions,
        embedding_provider=MockEmbeddingProvider(1024),
        search_provider=MockSearchProvider(),
    )
    run = await create_run(
        db_session,
        user_id,
        key="stage4-embedding-failure",
        message="[mock:tool-rag] [mock:embedding-error] 用本地上下文制定四周计划",
        settings=settings,
    )
    await AgentRunExecutor(sessions, MockPlanningProvider(), registry).execute(run.id)
    completed = await refresh_run(db_session, run.id)
    failures = list(
        await db_session.scalars(
            select(ToolCall).where(ToolCall.run_id == run.id, ToolCall.success.is_(False))
        )
    )
    assert completed.status == "completed"
    assert failures
    assert {item.error_code for item in failures} == {"TOOL_PROVIDER_UNAVAILABLE"}


def test_forged_evidence_reference_is_rejected() -> None:
    plan, context = candidate()
    forged_payload = plan.model_dump(mode="python")
    forged_payload["evidence_refs"] = [
        {"kind": "memory", "id": UUID("00000000-0000-0000-0000-000000000001")}
    ]
    forged = type(plan).model_validate(forged_payload)
    report = validate_candidate(
        forged,
        context,
        build_evidence_visibility(
            call_id="forged-evidence-test",
            evidence_catalog=[
            EvidenceCatalogItem(
                kind="memory",
                id=UUID("00000000-0000-0000-0000-000000000002"),
                title="allowed",
                content="allowed",
                reliability=1,
            )
            ],
        )[1],
    )
    source_check = next(check for check in report.checks if check.code == "SOURCE_INTEGRITY")
    assert source_check.passed is False


def test_memory_idempotency_only_maps_its_named_unique_constraint() -> None:
    unrelated = IntegrityError("statement", {}, Exception("ck_memories_status"))
    expected = IntegrityError(
        "statement",
        {},
        Exception("uq_memory_candidates_user_decision_idempotency"),
    )

    assert MemoryService._is_decision_idempotency_conflict(unrelated) is False  # noqa: SLF001
    assert MemoryService._is_decision_idempotency_conflict(expected) is True  # noqa: SLF001
