"""Tests for the rubric judge: parsing, deterministic scoring, LLM repair.

Pins:
* ``parse_rubric_output`` accepts plain and fenced JSON, rejects invalid.
* DeterministicRubricJudge: consecutive dates → horizon 5; broken dates
  → 1; forged evidence ref → grounding 1 (hard error); no refs → 3;
  valid refs → 5. Subjective dimensions stay None.
* OpenAICompatibleRubricJudge: parses a valid score, repairs once on a
  bad first answer, fails closed after a bad repair.
"""

from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest

from app.schemas.agent_runs import (
    EvidenceRef,
    PlanCandidate,
    TaskCandidate,
    WeeklyFocusCandidate,
)
from evals.v2.rubric_judge import (
    DeterministicRubricJudge,
    OpenAICompatibleRubricJudge,
    RubricJudgeInput,
    parse_rubric_output,
)


def _candidate(
    *,
    plan_date: date = date(2026, 8, 1),
    break_dates: bool = False,
    refs: list[EvidenceRef] | None = None,
) -> PlanCandidate:
    tasks = [
        TaskCandidate(
            title=f"任务 {index}",
            task_type="project",
            scheduled_date=plan_date + timedelta(days=index + 2 if break_dates else index),
            starter_action="完成第一步并记录",
            deliverable="一份可检查的产出",
            estimated_minutes=90,
        )
        for index in range(7)
    ]
    return PlanCandidate(
        plan_date=plan_date,
        horizon_start=plan_date,
        horizon_end=plan_date + timedelta(days=27),
        overall_direction="AI 应用工程师方向",
        weekly_focus=[
            WeeklyFocusCandidate(
                week_index=1, focus="补基础", success_signal="完成基础清单"
            )
        ],
        summary="第一周执行计划",
        rationale="根据画像生成",
        tasks=tasks,
        evidence_refs=refs or [],
    )


def _input(candidate: PlanCandidate, catalog: list[str] | None = None) -> RubricJudgeInput:
    return RubricJudgeInput(
        request_message="帮我制定四周求职计划",
        profile_summary="job_search / preparing / intermediate / 120min/day",
        time_budget_minutes=120,
        evidence_catalog_ids=catalog or [],
        candidate=candidate,
    )


def test_parse_plain_and_fenced_json() -> None:
    payload = (
        '{"goal_alignment": 4, "evidence_grounding": 3, "executability": 5, '
        '"horizon_compliance": 4, "rationales": {}}'
    )
    assert parse_rubric_output(payload) is not None
    assert parse_rubric_output(f"```json\n{payload}\n```") is not None
    assert parse_rubric_output("not json at all") is None


@pytest.mark.asyncio
async def test_deterministic_horizon_compliance() -> None:
    judge = DeterministicRubricJudge()
    good = await judge.score(_input(_candidate()))
    assert good.horizon_compliance == 5
    assert good.goal_alignment is None
    assert good.executability is None

    broken = await judge.score(_input(_candidate(break_dates=True)))
    assert broken.horizon_compliance == 1


@pytest.mark.asyncio
async def test_deterministic_evidence_grounding() -> None:
    judge = DeterministicRubricJudge()
    ref = EvidenceRef(
        kind="rag_document_chunk", id="00000000-0000-0000-0000-000000000001"
    )

    forged = await judge.score(_input(_candidate(refs=[ref]), catalog=[]))
    assert forged.evidence_grounding == 1

    none = await judge.score(_input(_candidate()))
    assert none.evidence_grounding == 3

    valid = await judge.score(
        _input(_candidate(refs=[ref]), catalog=[str(ref.id)])
    )
    assert valid.evidence_grounding == 5


def _llm_judge(handler) -> OpenAICompatibleRubricJudge:
    return OpenAICompatibleRubricJudge(
        api_key="test-only",
        base_url="https://judge.example.test/v1",
        model="gpt-test",
        transport=httpx.MockTransport(handler),
    )


_GOOD = (
    '{"goal_alignment": 4, "evidence_grounding": 5, "executability": 4, '
    '"horizon_compliance": 5, "rationales": {"goal_alignment": "匹配画像"}}'
)


@pytest.mark.asyncio
async def test_llm_judge_parses_and_repairs_once() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        body = _GOOD if len(calls) > 1 else "这不是JSON"
        return httpx.Response(
            200,
            request=request,
            json={
                "choices": [{"message": {"role": "assistant", "content": body}}]
            },
        )

    output = await _llm_judge(handler).score(_input(_candidate()))
    assert output.goal_alignment == 4
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_llm_judge_fails_closed_after_bad_repair() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": "还是不合法"}}]},
        )

    from app.agent.errors import ProviderUnavailableError

    with pytest.raises(ProviderUnavailableError):
        await _llm_judge(handler).score(_input(_candidate()))
