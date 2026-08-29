"""Batch 3 Claim Validation, formal Eval, and Audio contracts."""

import io
import wave
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_asr_provider
from app.models.agent_run import AgentEvent, AgentRun
from app.models.interview import InterviewSession, InterviewTurn
from app.providers.asr import ASRResult, ASRSegment
from app.schemas.interviews import AudioAnalysis
from app.services.interview_audio import _analysis
from evals.v2.interview_dataset import load_interview_dataset
from evals.v2.interview_runner import run_interview_cases
from tests.test_interview_api import create_materials
from tests.test_profile_api import bearer, guest_login


class BrokenASR:
    async def transcribe(self, **kwargs: object) -> ASRResult:
        del kwargs
        raise RuntimeError("provider unavailable")

    async def aclose(self) -> None:
        return None


def _wav() -> bytes:
    target = io.BytesIO()
    with wave.open(target, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\x00\x00" * 8000)
    return target.getvalue()


async def _ready_interview(
    db: AsyncSession, *, user_id: UUID, resume_id: UUID, target_id: UUID
) -> tuple[InterviewSession, InterviewTurn]:
    now = datetime.now(UTC)
    run = AgentRun(
        user_id=user_id,
        run_kind="interview_report",
        idempotency_key=f"batch3-report-{uuid4()}",
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
    interview = InterviewSession(
        user_id=user_id,
        resume_version_id=resume_id,
        job_target_id=target_id,
        interview_type="role_focused",
        status="completed",
        question_limit=4,
        followup_limit=2,
        asked_question_count=1,
        report_status="ready",
        report_version=1,
        report_json={
            "overall_summary": "已完成",
            "strengths": [],
            "weaknesses": [
                {
                    "weakness_key": "evidence",
                    "topic": "证据",
                    "dimension": "communication",
                    "severity": "low",
                    "confidence": 0.5,
                    "evidence_turn_ids": [str(uuid4())],
                    "status": "observed",
                }
            ],
            "dimension_summary": [],
            "recommended_training_actions": [
                {
                    "title": "练习",
                    "starter_action": "开始",
                    "deliverable": "回答",
                    "estimated_minutes": 10,
                    "source_weakness_keys": ["evidence"],
                }
            ],
            "comparison": None,
            "limitations": [],
        },
        report_run_id=run.id,
        idempotency_key=f"batch3-session-{uuid4()}",
        request_hash="a" * 64,
        completed_at=now,
    )
    db.add(interview)
    await db.flush()
    turn = InterviewTurn(
        user_id=user_id,
        session_id=interview.id,
        ordinal=1,
        topic_key="database",
        question_type="technical",
        question_text="如何用 PostgreSQL 索引优化查询？",
        question_sources_json=[
            {"kind": "job_target", "ref": str(target_id), "excerpt": "PostgreSQL"}
        ],
        question_fingerprint="b" * 64,
        answer_text="我用 PostgreSQL 索引和 EXPLAIN 将查询延迟降低 40%。",
        answer_status="submitted",
        analysis_status="ready",
        analysis_json={
            "covered_key_points": [],
            "missing_key_points": [],
            "factual_findings": [],
            "answer_structure": {
                "conclusion_first": True,
                "logical_flow": "clear",
                "specificity": "specific",
                "concision": "balanced",
            },
            "improvement_actions": [],
            "suggested_outline": [],
            "followup_reason": None,
            "limitations": [],
        },
        question_run_id=run.id,
        answered_at=now,
    )
    db.add(turn)
    await db.flush()
    report_json = cast(dict[str, object], interview.report_json)
    weaknesses = cast(list[dict[str, object]], report_json["weaknesses"])
    weaknesses[0]["evidence_turn_ids"] = [str(turn.id)]
    await db.flush()
    return interview, turn


@pytest.mark.asyncio
async def test_claim_assessment_is_traceable_and_user_scoped(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    token, user, _ = await guest_login(api_client)
    other_token, _, _ = await guest_login(api_client)
    resume_id, target_id = await create_materials(api_client, token, "batch3-claim")
    interview, turn = await _ready_interview(
        db_session,
        user_id=UUID(user),
        resume_id=UUID(resume_id),
        target_id=UUID(target_id),
    )
    response = await api_client.post(
        "/api/v1/resume-assessments",
        json={
            "resume_version_id": resume_id,
            "job_target_id": target_id,
            "interview_session_id": str(interview.id),
        },
        headers={**bearer(token), "Idempotency-Key": "batch3-assessment"},
    )
    assert response.status_code == HTTPStatus.CREATED
    assert all(item["evidence_turn_ids"] for item in response.json()["claims"])
    cited = {
        value for item in response.json()["claims"] for value in item["evidence_turn_ids"]
    }
    assert str(turn.id) in cited
    assessment_run = (
        await db_session.execute(
            select(AgentRun).where(
                AgentRun.user_id == UUID(user),
                AgentRun.run_kind == "resume_assessment",
            )
        )
    ).scalar_one()
    run_response = await api_client.get(
        f"/api/v1/agent-runs/{assessment_run.id}", headers=bearer(token)
    )
    assert run_response.status_code == HTTPStatus.OK
    assert run_response.json()["result"] == {
        "assessment_id": response.json()["assessment_id"],
        "claim_count": len(response.json()["claims"]),
    }
    terminal_events = (
        await db_session.execute(
            select(AgentEvent).where(AgentEvent.run_id == assessment_run.id)
        )
    ).scalars().all()
    assert [event.event_type for event in terminal_events] == ["run.completed"]
    hidden = await api_client.get(
        f"/api/v1/resume-assessments/{response.json()['assessment_id']}",
        headers=bearer(other_token),
    )
    assert hidden.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_resume_rewrite_requires_human_confirmation_and_creates_child_version(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    token, user, _ = await guest_login(api_client)
    resume_id, target_id = await create_materials(api_client, token, "batch3-rewrite")
    interview, _turn = await _ready_interview(
        db_session,
        user_id=UUID(user),
        resume_id=UUID(resume_id),
        target_id=UUID(target_id),
    )
    assessment_response = await api_client.post(
        "/api/v1/resume-assessments",
        json={
            "resume_version_id": resume_id,
            "job_target_id": target_id,
            "interview_session_id": str(interview.id),
        },
        headers={**bearer(token), "Idempotency-Key": "batch3-rewrite-assessment"},
    )
    assert assessment_response.status_code == HTTPStatus.CREATED
    assessment = assessment_response.json()
    claim = next(item for item in assessment["claims"] if item["suggested_rewrite"])
    apply_before_accept = await api_client.post(
        f"/api/v1/resume-assessments/{assessment['assessment_id']}"
        f"/claims/{claim['claim_id']}/apply",
        headers=bearer(token),
    )
    assert apply_before_accept.status_code == HTTPStatus.CONFLICT

    edited = f"{claim['claim_text']}，并补充了本人行动与可验证结果。"
    decision_response = await api_client.put(
        f"/api/v1/resume-assessments/{assessment['assessment_id']}"
        f"/claims/{claim['claim_id']}/decision",
        json={"status": "accepted", "rewrite_text": edited},
        headers=bearer(token),
    )
    assert decision_response.status_code == HTTPStatus.OK
    assert decision_response.json()["status"] == "accepted"

    apply_response = await api_client.post(
        f"/api/v1/resume-assessments/{assessment['assessment_id']}"
        f"/claims/{claim['claim_id']}/apply",
        headers=bearer(token),
    )
    assert apply_response.status_code == HTTPStatus.OK
    applied = apply_response.json()
    assert applied["decision"]["status"] == "applied"
    assert applied["resume_version"]["parent_version_id"] == resume_id
    assert edited in applied["resume_version"]["source_text"]
    assert applied["resume_version"]["resume_version_id"] != resume_id

    original_response = await api_client.get(
        "/api/v1/resume-versions", headers=bearer(token)
    )
    original = next(
        item for item in original_response.json()["items"] if item["resume_version_id"] == resume_id
    )
    assert edited not in original["source_text"]

    listed = await api_client.get("/api/v1/resume-assessments", headers=bearer(token))
    assert listed.status_code == HTTPStatus.OK
    assert listed.json()[0]["rewrite_decisions"][0]["status"] == "applied"


def test_audio_metrics_require_reliable_timestamps() -> None:
    reliable = _analysis(ASRResult(
        transcript="嗯 I improved PostgreSQL PostgreSQL",
        duration_seconds=6,
        segments=[
            ASRSegment(text="first", start_seconds=1, end_seconds=2),
            ASRSegment(text="second", start_seconds=5, end_seconds=6),
        ],
        confidence=0.9,
        timestamps_reliable=True,
    ))
    assert reliable.long_pause_count == 1
    assert reliable.preparation_seconds == 1
    assert reliable.effective_words_per_minute is not None
    transcript_only = AudioAnalysis(
        transcript="text",
        filler_count=0,
        repeated_phrase_count=0,
        timestamps_reliable=False,
    )
    assert transcript_only.long_pause_count is None
    assert transcript_only.effective_words_per_minute is None


def test_mock_asr_input_is_valid_wav() -> None:
    assert len(_wav()) > 1000


@pytest.mark.asyncio
async def test_audio_asr_failure_persists_fallback_text_without_media(
    api_client: AsyncClient,
    api_application: FastAPI,
    db_session: AsyncSession,
) -> None:
    token, user_id, _ = await guest_login(api_client)
    resume_id, target_id = await create_materials(api_client, token, "batch3-audio")
    now = datetime.now(UTC)
    question_run = AgentRun(
        user_id=UUID(user_id),
        run_kind="interview_start",
        idempotency_key=f"audio-question-{uuid4()}",
        request_text="question",
        hint_intent="interview_start",
        resolved_intent="interview_start",
        status="completed",
        result_kind="interview_turn",
        result_payload_json={
            "interview_id": str(uuid4()),
            "turn_id": str(uuid4()),
            "session_status": "active",
            "next_turn_id": None,
        },
        graph_version="test",
        config_snapshot_json={},
        deadline_at=now + timedelta(minutes=1),
        finished_at=now,
    )
    db_session.add(question_run)
    await db_session.flush()
    interview = InterviewSession(
        user_id=UUID(user_id),
        resume_version_id=UUID(resume_id),
        job_target_id=UUID(target_id),
        interview_type="role_focused",
        status="active",
        question_limit=4,
        followup_limit=2,
        asked_question_count=1,
        report_status="not_requested",
        idempotency_key=f"audio-session-{uuid4()}",
        request_hash="a" * 64,
        started_at=now,
    )
    db_session.add(interview)
    await db_session.flush()
    turn = InterviewTurn(
        user_id=UUID(user_id),
        session_id=interview.id,
        ordinal=1,
        topic_key="audio",
        question_type="technical",
        question_text="请回答",
        question_sources_json=[
            {"kind": "job_target", "ref": target_id, "excerpt": "Python"}
        ],
        question_fingerprint="b" * 64,
        question_run_id=question_run.id,
    )
    db_session.add(turn)
    await db_session.flush()
    interview.current_turn_id = turn.id
    await db_session.flush()
    api_application.dependency_overrides[get_asr_provider] = BrokenASR
    response = await api_client.post(
        f"/api/v1/interviews/{interview.id}/audio-answers",
        data={
            "turn_id": str(turn.id),
            "version": "1",
            "fallback_text": "这是保留的文本回答",
        },
        files={"audio": ("answer.wav", _wav(), "audio/wav")},
        headers={**bearer(token), "Idempotency-Key": "audio-fallback"},
    )
    assert response.status_code == HTTPStatus.ACCEPTED
    await db_session.refresh(turn)
    assert turn.answer_text == "这是保留的文本回答"
    assert turn.audio_analysis_json is not None
    assert "audio" not in {column.name for column in InterviewTurn.__table__.columns}


@pytest.mark.asyncio
async def test_formal_interview_eval_has_required_distribution() -> None:
    dataset = load_interview_dataset()
    assert len(dataset.cases) >= 16
    tags = [tag for case in dataset.cases for tag in case.tags]
    assert tags.count("question") >= 4
    assert tags.count("answer_analysis") >= 6
    assert tags.count("followup") >= 3
    assert tags.count("memory_plan") >= 2
    assert tags.count("safety") >= 1
    report = await run_interview_cases(dataset.cases)
    assert report.deterministic is True
    assert report.passed_count == report.case_count
    assert report.human_calibration_required is True
