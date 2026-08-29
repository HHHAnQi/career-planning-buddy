"""OpenAI-compatible Provider contract tests without external network access."""

import json
from collections.abc import Callable
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.agent.errors import (
    ProviderAuthenticationError,
    ProviderRateLimitError,
)
from app.agent.executor import AgentRunExecutor
from app.core.config import Settings
from app.harness.snapshots import SnapshotService
from app.models.plan import Plan
from app.providers.llm import (
    DirectLLMPlanningProvider,
    MockPlanningProvider,
    OpenAICompatiblePlanningProvider,
    build_planning_provider,
)
from app.schemas.agent_runs import AgentTurnResponse, MemoryContext, ProviderPlanResponse
from app.schemas.enums import ReplanMode
from tests.test_agent_nodes import candidate
from tests.test_agent_runtime import (
    create_run,
    create_user,
    refresh_run,
    runtime_factory,
)


def response_handler(
    content: str,
    *,
    status_code: int = 200,
) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://llm.example.test/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer unit-test-key"
        request_body: object = json.loads(request.content)
        assert isinstance(request_body, dict)
        assert request_body.get("max_tokens") == 1500
        assert "thinking" not in request_body
        response_format = request_body.get("response_format")
        assert isinstance(response_format, dict)
        assert response_format == {"type": "json_object"}
        messages = request_body.get("messages")
        assert isinstance(messages, list)
        user_message = messages[-1]
        assert isinstance(user_message, dict)
        assert '"output_schema"' in str(user_message.get("content"))
        return httpx.Response(
            status_code,
            request=request,
            headers={"x-request-id": "request-safe-id"},
            json={
                "id": "completion-safe-id",
                "model": "provider-model-id",
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 123, "completion_tokens": 45},
            },
        )

    return handle


def provider_for(
    handler: Callable[[httpx.Request], httpx.Response],
) -> OpenAICompatiblePlanningProvider:
    return OpenAICompatiblePlanningProvider(
        api_key="unit-test-key",
        base_url="https://llm.example.test/v1",
        model="configured-model",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_official_deepseek_disables_thinking_for_structured_output() -> None:
    plan, context = candidate()

    def handle(request: httpx.Request) -> httpx.Response:
        request_body: object = json.loads(request.content)
        assert isinstance(request_body, dict)
        assert request_body.get("thinking") == {"type": "disabled"}
        return httpx.Response(
            200,
            request=request,
            json={
                "model": "deepseek-v4-flash",
                "choices": [{"message": {"content": plan.model_dump_json()}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 200},
            },
        )

    provider = OpenAICompatiblePlanningProvider(
        api_key="unit-test-key",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        transport=httpx.MockTransport(handle),
    )

    raw = await provider.generate_plan(
        message="制定计划",
        context=context,
        replan_mode=ReplanMode.INITIAL,
        evidence_catalog=[],
    )

    assert ProviderPlanResponse.model_validate(raw).candidate == plan


@pytest.mark.asyncio
async def test_openai_compatible_provider_returns_validated_envelope_metadata() -> None:
    plan, context = candidate()
    provider = provider_for(response_handler(plan.model_dump_json()))

    raw = await provider.generate_plan(
        message="制定计划",
        context=context,
        replan_mode=ReplanMode.INITIAL,
        evidence_catalog=[],
    )
    response = ProviderPlanResponse.model_validate(raw)

    assert response.candidate == plan
    assert response.usage.provider == "openai_compatible"
    assert response.usage.model_id == "provider-model-id"
    assert response.usage.tokens_in == 123
    assert response.usage.tokens_out == 45
    assert response.usage.request_id == "request-safe-id"
    assert response.usage.raw_output_hash is not None


@pytest.mark.asyncio
async def test_direct_llm_provider_hides_tools_memory_and_evidence() -> None:
    plan, context = candidate()
    hidden_memory = "must-not-appear-in-direct-baseline"
    context = context.model_copy(
        update={
            "pinned_memories": [
                MemoryContext(
                    memory_id=uuid4(),
                    version=1,
                    memory_type="preference",
                    summary=hidden_memory,
                )
            ]
        }
    )

    def handle(request: httpx.Request) -> httpx.Response:
        body: object = json.loads(request.content)
        assert isinstance(body, dict)
        assert "tools" not in body
        messages = body.get("messages")
        assert isinstance(messages, list)
        rendered = json.dumps(messages, ensure_ascii=False)
        assert hidden_memory not in rendered
        # The system prompt may mention the (optional) retrieved_memories
        # section; what must stay hidden in the direct baseline is the
        # memory CONTENT itself, asserted above.
        assert hidden_memory not in rendered
        assert "<evidence_catalog>" not in rendered
        assert "<available_tools>" not in rendered
        return response_handler(plan.model_dump_json())(request)

    provider = DirectLLMPlanningProvider(provider_for(handle))
    raw = await provider.generate_agent_turn(
        message="制定计划",
        context=context,
        replan_mode=ReplanMode.INITIAL,
        available_tools=[],
        evidence_catalog=[],
        force_final=False,
    )

    assert AgentTurnResponse.model_validate(raw).final == plan


@pytest.mark.asyncio
async def test_invalid_json_can_be_repaired_once_through_explicit_provider_call() -> None:
    plan, context = candidate()
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        content = "not-json" if calls == 1 else plan.model_dump_json()
        return response_handler(content)(request)

    provider = provider_for(handle)
    raw = await provider.generate_plan(
        message="制定计划",
        context=context,
        replan_mode=ReplanMode.INITIAL,
        evidence_catalog=[],
    )
    assert "_raw_text" in raw
    repaired = await provider.repair_format(
        raw_output=raw,
        context=context,
        replan_mode=ReplanMode.INITIAL,
        evidence_catalog=[],
    )

    assert ProviderPlanResponse.model_validate(repaired).candidate == plan
    assert calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected_error"),
    [
        (401, ProviderAuthenticationError),
        (429, ProviderRateLimitError),
    ],
)
async def test_provider_http_failures_map_to_stable_errors(
    status_code: int,
    expected_error: type[Exception],
) -> None:
    _, context = candidate()
    provider = provider_for(response_handler("{}", status_code=status_code))

    with pytest.raises(expected_error):
        await provider.generate_plan(
            message="制定计划",
            context=context,
            replan_mode=ReplanMode.INITIAL,
            evidence_catalog=[],
        )


def test_provider_factory_switches_explicitly_without_fallback() -> None:
    mock_settings = Settings(_env_file=None, llm_provider="mock")
    real_settings = Settings(
        _env_file=None,
        llm_provider="openai_compatible",
        llm_api_key="unit-test-key",
        llm_base_url="https://llm.example.test/v1",
        llm_model="configured-model",
        agent_deadline_seconds=120,
    )

    assert isinstance(build_planning_provider(mock_settings), MockPlanningProvider)
    assert isinstance(
        build_planning_provider(real_settings),
        OpenAICompatiblePlanningProvider,
    )
    real_snapshot = SnapshotService.build_config(real_settings)
    assert real_snapshot.provider == "openai_compatible"
    assert real_snapshot.model_alias == "configured-model"
    assert real_snapshot.prompt_versions["career_planning"] == (
        "openai_compatible_plan_stage6_week_schedule_v6_memoryuse"
    )
    assert real_snapshot.node_timeouts_seconds["career_planning_agent"] == 120.0
    # The repair node is capped at 30s regardless of the run deadline —
    # a second full generation inheriting the full deadline is what broke
    # the 60s latency SLO (repair-03, SLO v1.4).
    assert real_snapshot.node_timeouts_seconds["revise_or_fallback"] == 30.0

    mock_snapshot = SnapshotService.build_config(mock_settings)
    assert mock_snapshot.node_timeouts_seconds["career_planning_agent"] == 30
    assert mock_snapshot.node_timeouts_seconds["revise_or_fallback"] == 12


@pytest.mark.asyncio
async def test_real_adapter_runs_through_graph_and_persists_safe_metadata(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    plan_candidate, _ = candidate()
    provider = provider_for(response_handler(plan_candidate.model_dump_json()))
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url="postgresql+asyncpg://career_buddy:career_buddy_local@localhost:5432/career_buddy",
        jwt_secret="test-secret-with-at-least-32-characters",
        llm_provider="openai_compatible",
        llm_api_key="unit-test-key",
        llm_base_url="https://llm.example.test/v1",
        llm_model="configured-model",
    )
    user_id = await create_user(db_session)
    run = await create_run(
        db_session,
        user_id,
        message="帮我制定未来五周的求职计划",
        key="real-adapter-graph",
        settings=settings,
    )

    await AgentRunExecutor(runtime_factory(db_connection), provider).execute(run.id)
    completed = await refresh_run(db_session, run.id)
    plan = await db_session.scalar(select(Plan).where(Plan.source_run_id == run.id))

    assert completed.status == "completed"
    assert completed.model_id == "provider-model-id"
    assert completed.config_snapshot_json["provider"] == "openai_compatible"
    assert plan is not None
    assert plan.metadata_json["provider"] == "openai_compatible"
    assert plan.metadata_json["model_id"] == "provider-model-id"
