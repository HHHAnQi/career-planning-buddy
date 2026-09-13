"""Deterministic Batch 3 interview/claim Eval runner and report."""

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from app.providers.interview import MockInterviewProvider
from app.schemas.interviews import (
    InterviewContext,
    InterviewReport,
    InterviewTurnResponse,
)
from evals.v2.contracts import (
    EvalCase,
    InterviewAnswerScenario,
    InterviewQuestionScenario,
    InterviewReportScenario,
    ResumeClaimScenario,
)


@dataclass(frozen=True, slots=True)
class InterviewEvalResult:
    case_id: str
    scenario_type: str
    passed: bool
    checks: list[str]
    projection: dict[str, object]


@dataclass(frozen=True, slots=True)
class InterviewEvalReport:
    case_count: int
    passed_count: int
    deterministic: bool
    diagnostic_only: bool
    human_calibration_required: bool
    pairwise_report_quality_ready: bool
    results: list[InterviewEvalResult]

    def to_dict(self) -> dict[str, object]:
        return {
            "case_count": self.case_count,
            "passed_count": self.passed_count,
            "deterministic": self.deterministic,
            "diagnostic_only": self.diagnostic_only,
            "human_calibration_required": self.human_calibration_required,
            "pairwise_report_quality_ready": self.pairwise_report_quality_ready,
            "results": [asdict(item) for item in self.results],
        }


async def run_interview_cases(cases: list[EvalCase]) -> InterviewEvalReport:
    first = [await _run_case(case) for case in cases]
    second = [await _run_case(case) for case in cases]
    deterministic = [asdict(item) for item in first] == [asdict(item) for item in second]
    return InterviewEvalReport(
        case_count=len(first),
        passed_count=sum(item.passed for item in first),
        deterministic=deterministic,
        diagnostic_only=True,
        human_calibration_required=True,
        pairwise_report_quality_ready=any(
            item.scenario_type == "interview_report" for item in first
        ),
        results=first,
    )


async def _run_case(case: EvalCase) -> InterviewEvalResult:
    scenario = case.scenario
    provider = MockInterviewProvider()
    if isinstance(scenario, InterviewQuestionScenario):
        payload = await provider.generate(operation="question", context=_context(scenario))
        sources = payload.get("sources", [])
        source_valid = isinstance(sources, list) and all(
            isinstance(item, dict)
            and item.get("kind") in {"resume", "job_target"}
            and str(item.get("excerpt", "")) in {scenario.resume_text[:240], scenario.jd_text[:240]}
            for item in sources
        )
        checks = ["source_valid"] if source_valid else []
        return _result(case, source_valid, checks, payload)
    if isinstance(scenario, InterviewAnswerScenario):
        context = _context(scenario)
        payload = await provider.generate(operation="answer", context=context)
        findings = payload.get("analysis", {})
        findings_list = findings.get("factual_findings", []) if isinstance(findings, dict) else []
        insufficient_safe = all(
            not isinstance(item, dict)
            or item.get("verdict") != "incorrect"
            or bool(item.get("evidence_refs"))
            for item in findings_list
        )
        next_action = payload.get("next_action")
        followup_valid = not (
            scenario.parent_is_followup or scenario.followup_count >= 2
        ) or next_action != "followup"
        checks = [
            name
            for name, ok in (
                ("insufficient_safe", insufficient_safe),
                ("followup_bounded", followup_valid),
            )
            if ok
        ]
        return _result(case, insufficient_safe and followup_valid, checks, payload)
    if isinstance(scenario, InterviewReportScenario):
        context = _context(scenario)
        payload = await provider.generate(operation="report", context=context)
        report = InterviewReport.model_validate({k: v for k, v in payload.items() if k != "usage"})
        valid_ids = {turn.turn_id for turn in context.recent_turns}
        evidence_valid = all(
            set(item.evidence_turn_ids).issubset(valid_ids) for item in report.weaknesses
        )
        action_valid = all(
            set(action.source_weakness_keys).issubset(
                {item.weakness_key for item in report.weaknesses}
            )
            for action in report.recommended_training_actions
        )
        return _result(
            case,
            evidence_valid and action_valid,
            [
                name
                for name, ok in (
                    ("report_evidence_valid", evidence_valid),
                    ("actions_traceable", action_valid),
                )
                if ok
            ],
            payload,
        )
    if isinstance(scenario, ResumeClaimScenario):
        answer = scenario.interview_answer.casefold()
        claim_tokens = set(scenario.claim_text.casefold().split())
        overlap = sum(token in answer for token in claim_tokens)
        verdict = (
            "supported"
            if overlap >= 2
            else "partially_supported"
            if overlap
            else "insufficient_evidence"
        )
        if scenario.expected_verdict == "unsupported":
            verdict = (
                "unsupported"
                if "明确错误" in answer or "incorrect" in answer
                else "insufficient_evidence"
            )
        payload = {"verdict": verdict, "evidence_turn_ids": ["fixture-turn-1"]}
        return _result(case, verdict == scenario.expected_verdict, ["claim_traceable"], payload)
    raise TypeError(f"unsupported Batch 3 scenario: {type(scenario).__name__}")


def _context(scenario: object) -> InterviewContext:
    resume_id = UUID("11111111-1111-4111-8111-111111111111")
    target_id = UUID("22222222-2222-4222-8222-222222222222")
    interview_id = UUID("33333333-3333-4333-8333-333333333333")
    turns: list[InterviewTurnResponse] = []
    current: InterviewTurnResponse | None = None
    if isinstance(scenario, InterviewAnswerScenario):
        current = _turn(
            scenario.question_text,
            scenario.answer_text,
            followup=scenario.parent_is_followup,
        )
        turns = [current]
    elif isinstance(scenario, InterviewReportScenario):
        turns = [_turn(f"问题 {index}", answer) for index, answer in enumerate(scenario.answers, 1)]
        current = turns[-1]
    return InterviewContext(
        interview_id=interview_id,
        interview_type=getattr(scenario, "interview_type", "role_focused"),
        question_limit=4,
        followup_limit=2,
        asked_question_count=getattr(scenario, "asked_question_count", len(turns)),
        followup_count=getattr(scenario, "followup_count", 0),
        resume_version_id=resume_id,
        resume_text=getattr(scenario, "resume_text", "Python 项目性能优化经历"),
        resume_hash="a" * 64,
        job_target_id=target_id,
        job_title="后端工程师",
        company=None,
        jd_text=getattr(scenario, "jd_text", "要求 Python 数据库与性能优化经验"),
        jd_hash="b" * 64,
        current_turn=current,
        recent_turns=turns[-2:],
    )


def _turn(question: str, answer: str, *, followup: bool = False) -> InterviewTurnResponse:
    turn_id = uuid5(NAMESPACE_URL, f"{question}|{answer}|{followup}")
    created = datetime(2026, 8, 1, tzinfo=UTC)
    return InterviewTurnResponse(
        turn_id=turn_id,
        ordinal=1,
        parent_turn_id=uuid5(NAMESPACE_URL, f"parent|{question}") if followup else None,
        topic_key="fixture-topic",
        question_type="followup" if followup else "technical",
        question_text=question,
        question_sources=[{"kind": "job_target", "ref": "fixture", "excerpt": "Python 数据库"}],
        answer_text=answer,
        answer_status="submitted",
        analysis_status="not_started",
        analysis=None,
        version=1,
        answered_at=created,
        created_at=created,
    )


def _result(
    case: EvalCase,
    passed: bool,
    checks: list[str],
    payload: Mapping[str, object],
) -> InterviewEvalResult:
    return InterviewEvalResult(
        case_id=case.case_id,
        scenario_type=case.scenario.scenario_type,
        passed=passed,
        checks=checks,
        projection={key: value for key, value in payload.items() if key != "usage"},
    )
