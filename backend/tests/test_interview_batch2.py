"""Batch 2 evidence, consent, training, retest, and comparison contracts."""

from datetime import UTC, datetime, timedelta
from typing import Literal, cast
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import product_today
from app.models.agent_run import AgentRun
from app.models.evidence import Memory
from app.models.interview import InterviewSession, InterviewTurn
from app.models.plan import Plan, Task, TaskAdjustmentProposal
from app.schemas.interviews import InterviewReport
from app.services.interview_persistence import InterviewPersistenceService
from tests.test_interview_api import create_materials
from tests.test_profile_api import bearer, guest_login, profile_body


def report(
    turn_ids: list[UUID],
    *,
    severity: Literal["low", "medium", "high"] = "medium",
    weakness_key: str = "database-index",
) -> dict[str, object]:
    return {
        "overall_summary": "数据库索引回答需要更多证据。",
        "strengths": [],
        "weaknesses": [
            {
                "weakness_key": weakness_key,
                "topic": "数据库索引设计",
                "dimension": "technical_depth",
                "severity": severity,
                "confidence": 0.8,
                "evidence_turn_ids": [str(item) for item in turn_ids],
                "status": "observed",
            }
        ],
        "dimension_summary": [],
        "recommended_training_actions": [
            {
                "title": "练习索引取舍",
                "starter_action": "步骤1：解释选择性；步骤2：写出索引方案",
                "deliverable": "一份带 EXPLAIN 结果的索引方案",
                "estimated_minutes": 30,
                "source_weakness_keys": [weakness_key],
            }
        ],
        "comparison": None,
        "limitations": [],
    }


async def ready_interview(
    db: AsyncSession,
    *,
    user_id: UUID | str,
    resume_id: UUID | str,
    target_id: UUID | str,
    evidence_count: int = 1,
) -> InterviewSession:
    user_id = UUID(str(user_id))
    resume_id = UUID(str(resume_id))
    target_id = UUID(str(target_id))
    now = datetime.now(UTC)
    run = AgentRun(
        user_id=user_id,
        run_kind="interview_report",
        idempotency_key=f"report-{uuid4()}",
        request_text="report",
        hint_intent="interview_report",
        resolved_intent="interview_report",
        status="completed",
        result_kind="interview_report",
        result_payload_json={"interview_id": str(uuid4()), "report_version": 1, "status": "ready"},
        graph_version="test",
        config_snapshot_json={},
        deadline_at=now + timedelta(minutes=1),
        finished_at=now,
    )
    db.add(run)
    await db.flush()
    session = InterviewSession(
        user_id=user_id,
        resume_version_id=resume_id,
        job_target_id=target_id,
        interview_type="role_focused",
        status="completed",
        question_limit=4,
        followup_limit=2,
        asked_question_count=evidence_count,
        report_status="ready",
        report_version=1,
        report_run_id=run.id,
        idempotency_key=f"session-{uuid4()}",
        request_hash="a" * 64,
        completed_at=now,
    )
    db.add(session)
    await db.flush()
    turns = []
    for ordinal in range(1, evidence_count + 1):
        turn = InterviewTurn(
            user_id=user_id,
            session_id=session.id,
            ordinal=ordinal,
            topic_key="database-index",
            question_type="technical",
            question_text="如何设计数据库索引？",
            question_sources_json=[
                {"kind": "job_target", "ref": str(target_id), "excerpt": "数据库"}
            ],
            question_fingerprint=(f"{ordinal}" * 64)[:64],
            answer_text="使用 B-tree 索引。",
            answer_status="submitted",
            analysis_status="ready",
            analysis_json={
                "covered_key_points": [],
                "missing_key_points": ["选择性"],
                "factual_findings": [],
                "answer_structure": {
                    "conclusion_first": True,
                    "logical_flow": "clear",
                    "specificity": "mixed",
                    "concision": "concise",
                },
                "improvement_actions": ["补充取舍"],
                "suggested_outline": [],
                "followup_reason": None,
                "limitations": [],
            },
            question_run_id=run.id,
        )
        db.add(turn)
        turns.append(turn)
    await db.flush()
    session.report_json = report([item.id for item in turns])
    run_payload = cast(dict[str, object], run.result_payload_json)
    run_payload["interview_id"] = str(session.id)
    await db.flush()
    return session


@pytest.mark.asyncio
async def test_one_observation_does_not_auto_create_candidate(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    token, user_id, _ = await guest_login(api_client)
    resume_id, target_id = await create_materials(api_client, token, "b2-one")
    session = await ready_interview(
        db_session,
        user_id=user_id,
        resume_id=resume_id,
        target_id=target_id,
        evidence_count=1,
    )
    response = await api_client.post(
        f"/api/v1/interviews/{session.id}/memory-candidates",
        json={"weakness_keys": []},
        headers=bearer(token),
    )
    assert response.status_code == 200
    assert response.json()["created_candidate_ids"] == []
    assert response.json()["skipped_weakness_keys"] == ["database-index"]


@pytest.mark.asyncio
async def test_multi_evidence_candidate_and_same_weakness_memory_merge(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    token, user_id, _ = await guest_login(api_client)
    resume_id, target_id = await create_materials(api_client, token, "b2-memory")
    session = await ready_interview(
        db_session, user_id=user_id, resume_id=resume_id, target_id=target_id, evidence_count=2
    )
    created = await api_client.post(
        f"/api/v1/interviews/{session.id}/memory-candidates",
        json={"weakness_keys": []},
        headers=bearer(token),
    )
    candidate_id = created.json()["created_candidate_ids"][0]
    confirmed = await api_client.post(
        f"/api/v1/memory-candidates/{candidate_id}/confirm",
        headers={**bearer(token), "Idempotency-Key": "confirm-b2-memory"},
    )
    assert confirmed.status_code == 200
    memory_id = confirmed.json()["memory"]["memory_id"]
    second = await ready_interview(
        db_session, user_id=user_id, resume_id=resume_id, target_id=target_id, evidence_count=1
    )
    second_created = await api_client.post(
        f"/api/v1/interviews/{second.id}/memory-candidates",
        json={"weakness_keys": []},
        headers=bearer(token),
    )
    second_candidate = second_created.json()["created_candidate_ids"][0]
    second_confirmed = await api_client.post(
        f"/api/v1/memory-candidates/{second_candidate}/confirm",
        headers={**bearer(token), "Idempotency-Key": "confirm-b2-memory-2"},
    )
    assert second_confirmed.json()["memory"]["memory_id"] == memory_id
    memories = list(await db_session.scalars(select(Memory).where(Memory.user_id == user_id)))
    assert len(memories) == 1
    assert memories[0].content_json["observation_count"] == 3


@pytest.mark.asyncio
async def test_batch_training_confirmation_applies_existing_task_proposal(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    token, user_id, _ = await guest_login(api_client)
    await api_client.put(
        "/api/v1/profile",
        json=profile_body(),
        headers={**bearer(token), "Idempotency-Key": "b2-profile"},
    )
    resume_id, target_id = await create_materials(api_client, token, "b2-training")
    session = await ready_interview(
        db_session, user_id=user_id, resume_id=resume_id, target_id=target_id, evidence_count=2
    )
    source_run = await db_session.scalar(select(AgentRun).where(AgentRun.user_id == user_id))
    assert source_run is not None
    today = product_today()
    plan = Plan(
        user_id=user_id,
        source_run_id=source_run.id,
        status="generated",
        plan_date=today,
        horizon_start=today,
        horizon_end=today + timedelta(days=7),
        overall_direction="面试训练",
        weekly_focus_json=[{"week_index": 1, "focus": "数据库", "success_signal": "完成练习"}],
        summary="原计划",
        rationale="原理由",
        assumptions_json=[],
        evidence_refs_json=[],
        metadata_json={},
    )
    db_session.add(plan)
    await db_session.flush()
    task = Task(
        plan_id=plan.id,
        user_id=user_id,
        title="旧任务",
        task_type="interview",
        scheduled_date=today,
        order_index=0,
        state="pending",
        starter_action="步骤1：旧动作；步骤2：旧交付",
        deliverable="旧交付",
        estimated_minutes=20,
    )
    db_session.add(task)
    await db_session.flush()
    preview = await api_client.post(
        f"/api/v1/interviews/{session.id}/training-actions/preview",
        json={"action_indexes": [0]},
        headers=bearer(token),
    )
    assert preview.json()["mode"] == "task_adjustment"
    confirmed = await api_client.post(
        f"/api/v1/interviews/{session.id}/training-actions/confirm",
        json={"action_indexes": [0]},
        headers={**bearer(token), "Idempotency-Key": "batch-training"},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["mode"] == "task_adjustment"
    await db_session.refresh(task)
    assert task.title == "练习索引取舍"
    proposal = await db_session.scalar(
        select(TaskAdjustmentProposal).where(TaskAdjustmentProposal.task_id == task.id)
    )
    assert proposal is not None and proposal.status == "applied"
    assert plan.status == "generated"


@pytest.mark.asyncio
async def test_batch_training_confirmation_starts_replan_without_silent_replacement(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    token, user_id, _ = await guest_login(api_client)
    await api_client.put(
        "/api/v1/profile",
        json=profile_body(),
        headers={**bearer(token), "Idempotency-Key": "b2-replan-profile"},
    )
    resume_id, target_id = await create_materials(api_client, token, "b2-replan")
    session = await ready_interview(
        db_session, user_id=user_id, resume_id=resume_id, target_id=target_id, evidence_count=2
    )
    source_run = await db_session.scalar(select(AgentRun).where(AgentRun.user_id == user_id))
    assert source_run is not None
    today = product_today()
    plan = Plan(
        user_id=user_id,
        source_run_id=source_run.id,
        status="completed",
        plan_date=today - timedelta(days=7),
        horizon_start=today - timedelta(days=7),
        horizon_end=today + timedelta(days=14),
        overall_direction="后端面试训练",
        weekly_focus_json=[
            {"week_index": 1, "focus": "数据库", "success_signal": "完成索引练习"},
            {"week_index": 2, "focus": "系统设计", "success_signal": "完成设计题"},
            {"week_index": 3, "focus": "复测", "success_signal": "完成复测"},
        ],
        summary="已结束的周期",
        rationale="历史训练",
        assumptions_json=[],
        evidence_refs_json=[],
        metadata_json={},
        completed_at=datetime.now(UTC),
    )
    db_session.add(plan)
    await db_session.flush()

    preview = await api_client.post(
        f"/api/v1/interviews/{session.id}/training-actions/preview",
        json={"action_indexes": [0]},
        headers=bearer(token),
    )
    assert preview.json()["mode"] == "replan"
    confirmed = await api_client.post(
        f"/api/v1/interviews/{session.id}/training-actions/confirm",
        json={"action_indexes": [0]},
        headers={**bearer(token), "Idempotency-Key": "batch-training-replan"},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["mode"] == "replan"
    run = await db_session.get(AgentRun, confirmed.json()["run"]["run_id"])
    assert run is not None
    assert run.source_interview_report_session_id == session.id
    assert run.source_plan_id == plan.id
    assert run.status == "pending"
    await db_session.refresh(plan)
    assert plan.status == "completed"


@pytest.mark.asyncio
async def test_retest_targets_weakness_and_comparison_requires_comparable_dimension(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    token, user_id, _ = await guest_login(api_client)
    resume_id, target_id = await create_materials(api_client, token, "b2-retest")
    baseline = await ready_interview(
        db_session, user_id=user_id, resume_id=resume_id, target_id=target_id, evidence_count=2
    )
    created = await api_client.post(
        f"/api/v1/interviews/{baseline.id}/retest",
        json={"weakness_keys": ["database-index"]},
        headers={**bearer(token), "Idempotency-Key": "retest-one"},
    )
    assert created.status_code == 202
    retest = await db_session.get(InterviewSession, created.json()["interview_id"])
    assert retest is not None
    assert retest.comparison_session_id == baseline.id
    assert retest.context_summary_json["retest_weakness_keys"] == ["database-index"]

    current_turn = uuid4()
    current_report = InterviewReport.model_validate(report([current_turn], weakness_key="other"))
    current_report = current_report.model_copy(
        update={
            "weaknesses": [
                current_report.weaknesses[0].model_copy(update={"dimension": "communication"})
            ]
        }
    )
    comparison = InterviewPersistenceService._comparison(
        baseline_id=baseline.id,
        current_id=retest.id,
        baseline=InterviewReport.model_validate(baseline.report_json),
        current=current_report,
        selected_keys={"database-index"},
    )
    assert comparison.items[0].status == "insufficient_comparable_evidence"


@pytest.mark.asyncio
async def test_other_user_cannot_use_batch2_report_endpoints(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    token_a, user_a, _ = await guest_login(api_client)
    token_b, _, _ = await guest_login(api_client)
    resume_id, target_id = await create_materials(api_client, token_a, "b2-isolation")
    session = await ready_interview(
        db_session, user_id=user_a, resume_id=resume_id, target_id=target_id, evidence_count=2
    )
    hidden = await api_client.post(
        f"/api/v1/interviews/{session.id}/training-actions/preview",
        json={"action_indexes": [0]},
        headers=bearer(token_b),
    )
    assert hidden.status_code == 404
