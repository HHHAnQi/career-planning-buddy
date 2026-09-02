"""Batch 1 structured output and hard AI-quality gates."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.agent.errors import StructuredOutputError
from app.agent.interview_graph import InterviewGraph
from app.agent.interview_nodes import validate_question, validate_report
from app.harness.snapshots import SnapshotService
from app.prompts.interview import interview_messages, repair_messages
from app.providers.interview import LLMInterviewProvider, MockInterviewProvider
from app.providers.llm_contracts import LLMRequest, LLMResponse, LLMUsage
from app.schemas.interviews import (
    FactualFinding,
    InterviewAnswerCandidate,
    InterviewContext,
    InterviewQuestionCandidate,
    InterviewReport,
    InterviewTurnResponse,
)


def context() -> InterviewContext:
    return InterviewContext(
        interview_id=uuid4(),
        interview_type="role_focused",
        question_limit=4,
        followup_limit=2,
        asked_question_count=0,
        followup_count=0,
        resume_version_id=uuid4(),
        resume_text="FastAPI 项目负责接口设计、PostgreSQL 数据建模和测试交付。",
        resume_hash="a" * 64,
        job_target_id=uuid4(),
        job_title="后端工程师",
        company="Example",
        jd_text="负责 Python 服务开发，要求 FastAPI、PostgreSQL 与自动化测试经验。",
        jd_hash="b" * 64,
    )


@pytest.mark.asyncio
async def test_mock_question_is_grounded_in_frozen_job_target() -> None:
    visible = context()
    raw = await MockInterviewProvider().generate(operation="question", context=visible)
    candidate = InterviewQuestionCandidate.model_validate(
        {key: value for key, value in raw.items() if key != "usage"}
    )
    assert validate_question(candidate, visible) == candidate
    assert candidate.sources[0].excerpt in visible.jd_text


def test_high_severity_error_requires_evidence_or_low_confidence() -> None:
    with pytest.raises(ValidationError):
        FactualFinding(
            claim="技术结论错误",
            verdict="incorrect",
            severity="high",
            confidence=0.9,
            rationale="没有提供依据",
        )


def test_question_rejects_forged_resume_excerpt() -> None:
    visible = context()
    forged = InterviewQuestionCandidate(
        topic_key="invented",
        question_type="project",
        question_text="请介绍你在虚构公司的项目经历。",
        sources=[
            {
                "kind": "resume",
                "ref": str(visible.resume_version_id),
                "excerpt": "虚构公司项目",
            }
        ],
    )
    with pytest.raises(StructuredOutputError):
        validate_question(forged, visible)


def test_report_rejects_cross_session_turn_reference() -> None:
    visible = context()
    report = InterviewReport(
        overall_summary="仅根据当前回答形成的报告。",
        weaknesses=[
            {
                "weakness_key": "evidence",
                "topic": "证据",
                "dimension": "communication",
                "severity": "medium",
                "confidence": 0.7,
                "evidence_turn_ids": [uuid4()],
            }
        ],
        recommended_training_actions=[
            {
                "title": "补充证据",
                "starter_action": "重写一次回答并标出事实依据。",
                "deliverable": "一份带依据的回答提纲",
                "estimated_minutes": 20,
                "source_weakness_keys": ["evidence"],
            }
        ],
    )
    with pytest.raises(StructuredOutputError):
        validate_report(report, visible)


def test_golden_case_catalog_has_twelve_review_cases() -> None:
    path = Path(__file__).parent / "fixtures" / "interview_golden_cases.json"
    cases = json.loads(path.read_text(encoding="utf-8"))
    assert len(cases) >= 12
    assert len({case["id"] for case in cases}) == len(cases)


class _RecordingClient:
    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(
            content='{"topic_key":"x","question_type":"technical"}',
            provider_id="openai_compatible",
            model_id="test-model",
            usage=LLMUsage(input_tokens=10, output_tokens=5),
            latency_ms=1,
            raw_output_hash="a" * 64,
        )

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_real_interview_provider_requests_strict_json() -> None:
    from app.core.config import Settings

    client = _RecordingClient()
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://localhost/test",
        jwt_secret="x" * 32,
        llm_provider="openai_compatible",
        llm_api_key="test-key",
        llm_base_url="https://example.test/v1",
        llm_model="test-model",
    )
    await LLMInterviewProvider(settings, client).generate(operation="question", context=context())
    assert client.requests[0].structured_output == "json_object"
    assert client.requests[0].tools == []


def test_interview_prompt_includes_operation_schema() -> None:
    system_message = interview_messages("question", context())[0].content

    assert "JSON Schema" in system_message
    assert '"question_text"' in system_message
    assert '"sources"' in system_message
    assert "resume_version_id" in system_message
    assert "job_target_id" in system_message
    assert "evidence_refs" in system_message
    assert "current_turn.turn_id" in system_message


def test_report_requires_actionable_evidence() -> None:
    with pytest.raises(ValidationError):
        InterviewReport.model_validate({"overall_summary": "没有可追溯结论。"})


def test_report_prompt_prohibits_using_skips_as_weakness_evidence() -> None:
    system_message = interview_messages("report", context())[0].content

    assert "skipped or unanswered turn is insufficient evidence" in system_message
    assert "Do not criticize information that is already present" in system_message
    repair_system_message = repair_messages("report", context(), {}, "bad evidence")[0].content
    assert "answer_status is submitted" in repair_system_message
    assert "must never support a weakness" in repair_system_message


def test_report_rejects_skipped_turn_as_weakness_evidence() -> None:
    from datetime import UTC, datetime

    visible = context()
    skipped = InterviewTurnResponse(
        turn_id=uuid4(),
        ordinal=2,
        parent_turn_id=uuid4(),
        topic_key="database",
        question_type="followup",
        question_text="请补充。",
        question_sources=[],
        answer_text=None,
        answer_status="skipped",
        analysis_status="not_started",
        analysis=None,
        version=2,
        answered_at=None,
        created_at=datetime.now(UTC),
    )
    visible = visible.model_copy(update={"recent_turns": [skipped]})
    report = InterviewReport.model_validate(
        {
            "overall_summary": "证据不足。",
            "weaknesses": [
                {
                    "weakness_key": "skip",
                    "topic": "跳过",
                    "dimension": "technical",
                    "severity": "medium",
                    "confidence": 0.8,
                    "evidence_turn_ids": [skipped.turn_id],
                }
            ],
            "recommended_training_actions": [
                {
                    "title": "练习",
                    "starter_action": "补充回答",
                    "deliverable": "回答稿",
                    "estimated_minutes": 20,
                    "source_weakness_keys": ["skip"],
                }
            ],
        }
    )

    with pytest.raises(StructuredOutputError):
        validate_report(report, visible)


def test_report_rejects_claim_that_supplied_metrics_are_missing() -> None:
    from datetime import UTC, datetime

    visible = context()
    answered = InterviewTurnResponse(
        turn_id=uuid4(),
        ordinal=1,
        parent_turn_id=None,
        topic_key="performance",
        question_type="project",
        question_text="如何验证性能改善？",
        question_sources=[],
        answer_text="压测验证 P95 从 420ms 降到 180ms，并比较执行计划。",
        answer_status="submitted",
        analysis_status="ready",
        analysis=None,
        version=2,
        answered_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )
    visible = visible.model_copy(update={"recent_turns": [answered]})
    report = InterviewReport.model_validate(
        {
            "overall_summary": "The answer lacked performance metrics.",
            "weaknesses": [
                {
                    "weakness_key": "metrics",
                    "topic": "missing metrics",
                    "dimension": "communication",
                    "severity": "medium",
                    "confidence": 0.8,
                    "evidence_turn_ids": [answered.turn_id],
                }
            ],
            "recommended_training_actions": [
                {
                    "title": "补充指标",
                    "starter_action": "列出性能指标",
                    "deliverable": "指标清单",
                    "estimated_minutes": 10,
                    "source_weakness_keys": ["metrics"],
                }
            ],
        }
    )

    with pytest.raises(StructuredOutputError, match="contradicts supplied answer"):
        validate_report(report, visible)


def test_real_interview_snapshot_allows_one_repair_window() -> None:
    from app.core.config import Settings

    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://localhost/test",
        jwt_secret="x" * 32,
        llm_provider="openai_compatible",
        llm_api_key="test-key",
        llm_base_url="https://example.test/v1",
        llm_model="test-model",
        llm_timeout_seconds=30,
        agent_deadline_seconds=45,
    )

    config = SnapshotService.build_interview_config(settings)

    assert config.deadline_seconds == 120
    assert config.max_total_tokens == 22500
    assert config.node_timeouts_seconds["interview_generate"] == 120
    assert config.node_timeouts_seconds["interview_validate"] == 120


def test_answer_finding_evidence_is_bound_to_authoritative_turn() -> None:
    from datetime import UTC, datetime

    visible = context()
    turn_id = uuid4()
    turn = InterviewTurnResponse(
        turn_id=turn_id,
        ordinal=1,
        parent_turn_id=None,
        topic_key="database",
        question_type="technical",
        question_text="如何定位慢查询？",
        question_sources=[],
        answer_text="使用 EXPLAIN ANALYZE。",
        answer_status="submitted",
        analysis_status="running",
        analysis=None,
        version=2,
        answered_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )
    visible = visible.model_copy(update={"current_turn": turn, "recent_turns": [turn]})
    answer = InterviewAnswerCandidate.model_validate(
        {
            "analysis": {
                "covered_key_points": [],
                "missing_key_points": [],
                "factual_findings": [
                    {
                        "claim": "使用执行计划",
                        "verdict": "correct",
                        "severity": "low",
                        "confidence": 0.8,
                        "rationale": "回答中明确说明。",
                        "evidence_refs": ["resume_text"],
                    }
                ],
                "answer_structure": {
                    "conclusion_first": True,
                    "logical_flow": "clear",
                    "specificity": "specific",
                    "concision": "concise",
                },
                "improvement_actions": [],
                "suggested_outline": [],
                "limitations": [],
            },
            "next_action": "finish",
        }
    )

    bound = InterviewGraph._bind_answer_evidence(answer, visible)

    assert bound.analysis.factual_findings[0].evidence_refs == [str(turn_id)]


def test_followup_answer_cannot_create_second_level_followup() -> None:
    from datetime import UTC, datetime

    visible = context()
    turn = InterviewTurnResponse(
        turn_id=uuid4(),
        ordinal=2,
        parent_turn_id=uuid4(),
        topic_key="database",
        question_type="followup",
        question_text="请补充执行计划。",
        question_sources=[],
        answer_text="补充说明。",
        answer_status="submitted",
        analysis_status="running",
        analysis=None,
        version=2,
        answered_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )
    visible = visible.model_copy(update={"current_turn": turn, "recent_turns": [turn]})
    answer = InterviewAnswerCandidate.model_validate(
        {
            "analysis": {
                "answer_structure": {
                    "conclusion_first": True,
                    "logical_flow": "clear",
                    "specificity": "specific",
                    "concision": "concise",
                }
            },
            "next_action": "followup",
            "next_question": {
                "topic_key": "database",
                "question_type": "followup",
                "question_text": "请继续补充。",
                "sources": [
                    {
                        "kind": "answer",
                        "ref": str(turn.turn_id),
                        "excerpt": "补充说明。",
                    }
                ],
                "parent_turn_id": turn.turn_id,
            },
        }
    )

    bound = InterviewGraph._bind_answer_evidence(answer, visible)

    assert bound.next_action == "finish"
    assert bound.next_question is None

    alternate = answer.model_copy(update={"next_action": "next"})
    alternate_bound = InterviewGraph._bind_answer_evidence(alternate, visible)
    assert alternate_bound.next_action == "finish"
    assert alternate_bound.next_question is None
