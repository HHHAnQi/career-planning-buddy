"""Candidate-level evidence visibility and persistence integrity tests."""

from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.agent.executor import AgentRunExecutor
from app.agent.nodes import validate_candidate
from app.harness.evidence import build_evidence_visibility
from app.models.evidence import Memory
from app.models.plan import Plan
from app.prompts.career_planning import business_repair_messages, format_repair_messages
from app.providers.llm import MockPlanningProvider
from app.schemas.agent_runs import EvidenceCatalogItem, EvidenceRef
from app.schemas.enums import ReplanMode
from app.tools.contracts import EvidenceItem, ToolResult
from app.tools.registry import ToolRegistry
from tests.test_agent_nodes import candidate
from tests.test_agent_runtime import create_run, create_user, refresh_run, runtime_factory


def _evidence(value: int) -> EvidenceCatalogItem:
    return EvidenceCatalogItem(
        kind="memory",
        id=UUID(int=value),
        title=f"memory-{value}",
        content=f"visible evidence {value}",
        reliability=0.9,
    )


def test_forged_and_not_visible_refs_are_rejected_but_empty_refs_pass() -> None:
    plan, context = candidate()
    first = _evidence(1)
    second = _evidence(2)
    _visible_catalog, visibility = build_evidence_visibility(
        call_id="candidate-call",
        evidence_catalog=[first, second],
        visible_limit=1,
    )

    forged = plan.model_copy(
        update={"evidence_refs": [EvidenceRef(kind="memory", id=UUID(int=999))]}
    )
    hidden = plan.model_copy(
        update={"evidence_refs": [EvidenceRef(kind="memory", id=second.id)]}
    )

    assert not validate_candidate(forged, context, visibility).passed
    assert not validate_candidate(hidden, context, visibility).passed
    assert validate_candidate(plan, context, visibility).passed
    assert plan.evidence_refs == []
    assert visibility.visible_refs == [EvidenceRef(kind="memory", id=first.id)]
    assert visibility.truncated_refs == [EvidenceRef(kind="memory", id=second.id)]


def test_format_and_business_repair_prompts_receive_visible_catalog() -> None:
    plan, context = candidate()
    evidence = _evidence(7)

    format_messages = format_repair_messages(
        raw_output={"_raw_text": "invalid"},
        context=context,
        replan_mode=ReplanMode.INITIAL,
        evidence_catalog=[evidence],
    )
    business_messages = business_repair_messages(
        candidate=plan,
        context=context,
        repair_instructions=["Repair deterministic rule SOURCE_INTEGRITY"],
        message="repair",
        replan_mode=ReplanMode.INITIAL,
        evidence_catalog=[evidence],
    )

    assert str(evidence.id) in format_messages[-1]["content"]
    assert str(evidence.id) in business_messages[-1]["content"]


def test_evidence_removed_by_tool_compression_cannot_become_visible() -> None:
    evidence = EvidenceItem(
        kind="memory",
        id=UUID(int=8),
        title="oversized",
        content="x" * 500,
        reliability=0.9,
    )
    result = ToolResult(
        tool_name="memory_lookup",
        data={"items": [{"memory_id": str(evidence.id), "content": "x" * 500}]},
        evidence=[evidence],
    )

    compressed = ToolRegistry._compress(result, max_chars=1)

    assert compressed.truncated is True
    assert compressed.evidence == []


@pytest.mark.asyncio
async def test_cross_user_forged_memory_is_repaired_once_and_never_persisted(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    user_id = await create_user(db_session)
    other_user_id = await create_user(db_session)
    forged_id = UUID("00000000-0000-0000-0000-000000000001")
    db_session.add(
        Memory(
            id=forged_id,
            user_id=other_user_id,
            memory_type="profile_fact",
            summary="other user's memory",
            content_json={"pinned": True},
            sensitivity="normal",
            status="active",
            embedding=None,
        )
    )
    await db_session.flush()
    run = await create_run(
        db_session,
        user_id,
        key="evidence-cross-user",
        message="[mock:forged-evidence] 制定四周计划",
    )
    provider = MockPlanningProvider()

    await AgentRunExecutor(runtime_factory(db_connection), provider).execute(run.id)

    completed = await refresh_run(db_session, run.id)
    plan = await db_session.scalar(select(Plan).where(Plan.source_run_id == run.id))
    assert completed.status == "completed"
    assert provider.business_repair_calls == 1
    assert plan is not None
    assert plan.evidence_refs_json == []
