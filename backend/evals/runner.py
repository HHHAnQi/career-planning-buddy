"""Fixed-dataset Stage 5 Eval runner and deterministic graders."""

import json
from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from time import monotonic
from typing import Literal
from uuid import UUID

from pydantic import Field, ValidationError

from app.agent.nodes import (
    build_clarification,
    build_planning_context,
    build_safe_response,
    fallback_candidate,
    risk_gate,
    route_intent,
    validate_candidate,
)
from app.providers.llm import MockPlanningProvider
from app.schemas.agent_runs import (
    AgentTurnResponse,
    PlanCandidate,
    PlanContext,
    PlanFocusContext,
    ProfileContext,
    ProviderPlanResponse,
    ReviewContext,
)
from app.schemas.base import StrictModel
from app.schemas.enums import ReplanMode
from app.tools.contracts import ModelToolSpec

EVAL_ROOT = Path(__file__).resolve().parent
DATASET_PATH = EVAL_ROOT / "datasets" / "stage5-v1.jsonl"
ARTIFACT_ROOT = EVAL_ROOT / "artifacts"
BAD_CASE_ROOT = EVAL_ROOT / "bad_cases"
GRADERS = (
    "intent",
    "terminal_result",
    "schema_horizon",
    "budget",
    "startability",
    "deliverable",
    "source_integrity",
    "tool_policy",
    "continuity",
    "safety",
    "quality_reviewer",
)


class EvalProfile(StrictModel):
    goal_type: Literal["job_search", "internship", "career_change", "skill_growth"]
    stage: Literal["exploring", "preparing", "applying", "interviewing"]
    time_budget_minutes: int = Field(ge=15, le=480)
    skill_level: Literal["beginner", "intermediate", "advanced"]


class EvalCase(StrictModel):
    case_id: str
    category: str
    message: str
    profile: EvalProfile | None
    hint_intent: Literal["create_plan", "replan"] | None = None
    replan_mode: Literal["continue", "adjust"] | None = None
    expected_result_kind: Literal["plan", "clarification", "safe_response", "navigation"]
    expected_tools: list[Literal["memory_lookup", "rag_retrieve", "web_search"]] = Field(
        default_factory=list, max_length=2
    )


def load_cases(limit: int | None = None) -> list[EvalCase]:
    lines = DATASET_PATH.read_text(encoding="utf-8").splitlines()
    cases = [EvalCase.model_validate_json(line) for line in lines if line.strip()]
    if len(cases) != 30:
        raise RuntimeError(f"stage5-v1 must contain exactly 30 cases, found {len(cases)}")
    return cases[:limit] if limit is not None else cases


async def run_evaluation(
    *, case_limit: int | None = None, persist: bool = True
) -> dict[str, object]:
    cases = load_cases(case_limit)
    started = monotonic()
    results = [await _evaluate_case(case) for case in cases]
    grader_totals: dict[str, list[bool]] = defaultdict(list)
    status_counts: dict[str, int] = defaultdict(int)
    fallback_counts: dict[str, int] = defaultdict(int)
    total_tokens_in = 0
    total_tokens_out = 0
    total_latency_ms = 0
    total_tool_calls = 0
    for result in results:
        status_counts[str(result["status"])] += 1
        fallback = result.get("fallback_reason")
        if fallback is not None:
            fallback_counts[str(fallback)] += 1
        total_tokens_in += _integer(result["tokens_in"])
        total_tokens_out += _integer(result["tokens_out"])
        total_latency_ms += _integer(result["latency_ms"])
        total_tool_calls += _integer(result["tool_calls"])
        graders = result["graders"]
        if isinstance(graders, dict):
            for name, passed in graders.items():
                grader_totals[name].append(bool(passed))
    passed_cases = sum(bool(result["passed"]) for result in results)
    experiment_id = (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-")
        + sha256("|".join(str(result["case_id"]) for result in results).encode()).hexdigest()[:8]
    )
    report: dict[str, object] = {
        "experiment_id": experiment_id,
        "dataset_id": "stage5-v1",
        "provider": "mock",
        "deterministic": True,
        "case_count": len(results),
        "passed_cases": passed_cases,
        "failed_cases": len(results) - passed_cases,
        "pass_rate": round(passed_cases / len(results), 4),
        "grader_pass_rates": {
            name: round(sum(values) / len(values), 4)
            for name, values in sorted(grader_totals.items())
        },
        "status_counts": dict(sorted(status_counts.items())),
        "fallback_counts": dict(sorted(fallback_counts.items())),
        "averages": {
            "tokens_in": round(total_tokens_in / len(results), 2),
            "tokens_out": round(total_tokens_out / len(results), 2),
            "tool_calls": round(total_tool_calls / len(results), 2),
            "cost_cny": 0,
            "latency_ms": round(total_latency_ms / len(results), 2),
        },
        "wall_latency_ms": int((monotonic() - started) * 1000),
        "results": results,
    }
    if persist:
        _persist_report(report)
    return report


async def _evaluate_case(case: EvalCase) -> dict[str, object]:
    started = monotonic()
    provider = MockPlanningProvider()
    risk = risk_gate(case.message)
    actual_kind: str
    status: str
    fallback_reason: str | None = None
    candidate: PlanCandidate | None = None
    validation = None
    format_repairs = 0
    business_repairs = 0
    actual_tools: list[str] = []

    if risk.level == "high":
        build_safe_response()
        actual_kind = "safe_response"
        status = "degraded"
        fallback_reason = "high_risk_safe_response"
    else:
        profile = _profile(case.profile) if case.profile is not None else None
        intent = route_intent(
            message=case.message,
            hint_intent=case.hint_intent,
            profile=profile,
            source_plan_exists=case.hint_intent == "replan",
            forced_replan_mode=ReplanMode(case.replan_mode) if case.replan_mode else None,
        )
        if intent.missing_slots:
            build_clarification(intent)
            actual_kind = "clarification"
            status = "degraded"
            fallback_reason = "profile_incomplete"
        else:
            assert profile is not None
            source_plan = _source_plan(profile) if case.hint_intent == "replan" else None
            source_review = _source_review(case.replan_mode) if source_plan else None
            context = build_planning_context(
                profile=profile,
                requested_horizon_weeks=intent.requested_horizon_weeks,
                source_plan_id=source_plan.plan_id if source_plan else None,
                source_plan_version=source_plan.version if source_plan else None,
                source_plan=source_plan,
                source_review=source_review,
                completed_facts=[],
                planning_date=date(2026, 8, 1),
            )
            raw: Mapping[str, object]
            if case.expected_tools:
                specs = [
                    ModelToolSpec(
                        name=name,
                        description=f"Offline Eval specification for {name}",
                        input_json_schema={"type": "object"},
                        contract_version="1.0",
                    )
                    for name in ("memory_lookup", "rag_retrieve", "web_search")
                ]
                tool_turn = AgentTurnResponse.model_validate(
                    await provider.generate_agent_turn(
                        message=case.message,
                        context=context,
                        replan_mode=intent.replan_mode,
                        available_tools=specs,
                        evidence_catalog=[],
                        force_final=False,
                    )
                )
                actual_tools = [call.name for call in tool_turn.tool_calls]
                final_turn = AgentTurnResponse.model_validate(
                    await provider.generate_agent_turn(
                        message=case.message,
                        context=context,
                        replan_mode=intent.replan_mode,
                        available_tools=[],
                        evidence_catalog=[],
                        force_final=True,
                    )
                )
                if final_turn.final is None:
                    raise RuntimeError("Mock Eval final Tool turn did not contain a plan")
                raw = ProviderPlanResponse(
                    candidate=final_turn.final,
                    usage=final_turn.usage,
                ).model_dump(mode="json")
            else:
                raw = await provider.generate_plan(
                    message=case.message,
                    context=context,
                    replan_mode=intent.replan_mode,
                    evidence_catalog=[],
                )
            try:
                response = ProviderPlanResponse.model_validate(raw)
            except ValidationError:
                format_repairs = 1
                raw = await provider.repair_format(
                    raw_output=raw,
                    context=context,
                    replan_mode=intent.replan_mode,
                    evidence_catalog=[],
                )
                try:
                    response = ProviderPlanResponse.model_validate(raw)
                except ValidationError:
                    candidate = fallback_candidate(context, intent.replan_mode)
                    fallback_reason = "format_repair_failed"
                else:
                    candidate = response.candidate
            else:
                candidate = response.candidate
            validation = validate_candidate(candidate, context)
            if not validation.passed and fallback_reason is None:
                business_repairs = 1
                repaired_raw = await provider.repair_business_rules(
                    candidate=candidate,
                    context=context,
                    repair_instructions=validation.repair_instructions,
                    message=case.message,
                    replan_mode=intent.replan_mode,
                    evidence_catalog=[],
                )
                try:
                    repaired = ProviderPlanResponse.model_validate(repaired_raw).candidate
                except ValidationError:
                    repaired = fallback_candidate(context, intent.replan_mode)
                repaired_validation = validate_candidate(repaired, context)
                if repaired_validation.passed:
                    candidate = repaired
                    validation = repaired_validation
                else:
                    candidate = fallback_candidate(context, intent.replan_mode)
                    validation = validate_candidate(candidate, context)
                    fallback_reason = "business_rule_fallback"
            actual_kind = "plan"
            status = "degraded" if fallback_reason else "completed"

    checks = {check.code: check.passed for check in validation.checks} if validation else {}
    graders = {
        "intent": actual_kind == case.expected_result_kind,
        "terminal_result": status in {"completed", "degraded"},
        "schema_horizon": candidate is None or checks.get("HORIZON_MATCH", False),
        "budget": candidate is None or checks.get("TIME_BUDGET", False),
        "startability": candidate is None or checks.get("STARTER_ACTION", False),
        "deliverable": candidate is None or checks.get("DELIVERABLE", False),
        "source_integrity": candidate is None or checks.get("SOURCE_INTEGRITY", False),
        "tool_policy": (
            actual_tools == case.expected_tools
            and len(actual_tools) <= 2
            and set(actual_tools) <= {"memory_lookup", "rag_retrieve", "web_search"}
        ),
        "continuity": candidate is None or checks.get("REPLAN_CONTINUITY", False),
        "safety": (risk.level == "high") == (actual_kind == "safe_response"),
        "quality_reviewer": _quality_review(candidate, actual_kind),
    }
    return {
        "case_id": case.case_id,
        "category": case.category,
        "status": status,
        "result_kind": actual_kind,
        "fallback_reason": fallback_reason,
        "format_repairs": format_repairs,
        "business_repairs": business_repairs,
        "tool_calls": len(actual_tools),
        "tokens_in": (
            provider.plan_calls * 200
            + provider.format_repair_calls * 180
            + provider.business_repair_calls * 250
        ),
        "tokens_out": (
            provider.plan_calls * 350
            + provider.format_repair_calls * 320
            + provider.business_repair_calls * 400
        ),
        "latency_ms": int((monotonic() - started) * 1000),
        "graders": graders,
        "passed": all(graders.values()),
    }


def _profile(value: EvalProfile) -> ProfileContext:
    goal_mapping = {
        "job_search": "ai_backend",
        "internship": "agent_app",
        "career_change": "backend_java",
        "skill_growth": "fullstack",
    }
    return ProfileContext(
        user_id=UUID("00000000-0000-0000-0000-000000000100"),
        version=1,
        goal_type=goal_mapping[value.goal_type],
        stage=value.stage,
        time_budget_minutes=value.time_budget_minutes,
        skill_level=value.skill_level,
        skill_summary="Stage 5 deterministic evaluation profile",
    )


def _source_plan(profile: ProfileContext) -> PlanContext:
    return PlanContext(
        plan_id=UUID("00000000-0000-0000-0000-000000000200"),
        version=1,
        status="active",
        plan_date=date(2026, 7, 25),
        horizon_start=date(2026, 7, 25),
        horizon_end=date(2026, 8, 21),
        overall_direction=f"Continue evidence-driven preparation for {profile.goal_type.value}",
        weekly_focus=[
            PlanFocusContext(
                week_index=index,
                focus=f"Source focus week {index}",
                success_signal=f"Source evidence week {index}",
            )
            for index in range(1, 5)
        ],
    )


def _source_review(mode: str | None) -> ReviewContext:
    adjusted = mode == "adjust"
    return ReviewContext(
        review_id=UUID("00000000-0000-0000-0000-000000000300"),
        review_date=date(2026, 7, 31),
        blockers="Daily time budget changed" if adjusted else None,
        adjustment_request="Reduce scope and adjust the plan" if adjusted else None,
        replan_reason="time budget changed" if adjusted else None,
    )


def _quality_review(candidate: PlanCandidate | None, result_kind: str) -> bool:
    """Offline shadow reviewer; deterministic rules remain authoritative."""
    if candidate is None:
        return result_kind in {"clarification", "safe_response"}
    return bool(candidate.summary.strip() and candidate.rationale.strip() and candidate.tasks)


def _integer(value: object) -> int:
    if not isinstance(value, int):
        raise TypeError("evaluation metric must be an integer")
    return value


def _persist_report(report: dict[str, object]) -> None:
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    experiment_id = str(report["experiment_id"])
    (ARTIFACT_ROOT / f"{experiment_id}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    results = report.get("results")
    failed = (
        [item for item in results if isinstance(item, dict) and not item.get("passed")]
        if isinstance(results, list)
        else []
    )
    if failed:
        # A bad-case file is written ONLY when the experiment has actual
        # failures, so every file in bad_cases/ is meaningful.
        BAD_CASE_ROOT.mkdir(parents=True, exist_ok=True)
        (BAD_CASE_ROOT / f"{experiment_id}.jsonl").write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in failed),
            encoding="utf-8",
        )


def load_experiment(experiment_id: str) -> dict[str, object] | None:
    safe_name = Path(experiment_id).name
    if safe_name != experiment_id:
        return None
    path = ARTIFACT_ROOT / f"{safe_name}.json"
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else None
