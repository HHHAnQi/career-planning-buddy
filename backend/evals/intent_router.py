"""Deterministic, standalone evaluation for the intent routing contract."""

from collections import Counter
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.agent.nodes import INTENT_ROUTER_VERSION, build_clarification, route_intent
from app.schemas.agent_runs import ProfileContext
from app.schemas.base import StrictModel
from app.schemas.enums import CareerStage, GoalType, ReplanMode, RunIntent, SkillLevel

DATASET_PATH = Path(__file__).resolve().parent / "datasets" / "intent-routing-v1.jsonl"


class IntentEvalCase(StrictModel):
    case_id: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=2000)
    hint_intent: Literal["create_plan", "replan"] | None = None
    source_plan_exists: bool
    profile_exists: bool
    forced_replan_mode: ReplanMode | None = None
    expected_intent: RunIntent
    expected_mode: ReplanMode
    expected_reason: Literal[
        "profile_incomplete", "unsupported_intent", "intent_uncertain"
    ] | None
    expected_fresh: bool


def load_intent_cases(path: Path = DATASET_PATH) -> list[IntentEvalCase]:
    cases = [
        IntentEvalCase.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    case_ids = [case.case_id for case in cases]
    if len(cases) < 20 or len(case_ids) != len(set(case_ids)):
        raise RuntimeError("intent-routing-v1 requires at least 20 uniquely identified cases")
    return cases


def evaluate_intent_router(path: Path = DATASET_PATH) -> dict[str, object]:
    cases = load_intent_cases(path)
    failures: list[dict[str, object]] = []
    expected_counts: Counter[str] = Counter()
    for case in cases:
        result = route_intent(
            message=case.message,
            hint_intent=case.hint_intent,
            profile=_profile() if case.profile_exists else None,
            source_plan_exists=case.source_plan_exists,
            forced_replan_mode=case.forced_replan_mode,
        )
        reason = None
        if result.intent == RunIntent.UNSUPPORTED or result.missing_slots:
            reason = build_clarification(result).reason
        expected_counts[case.expected_intent.value] += 1
        actual = {
            "intent": result.intent.value,
            "mode": result.replan_mode.value,
            "reason": reason,
            "fresh": result.requires_fresh_information,
        }
        expected = {
            "intent": case.expected_intent.value,
            "mode": case.expected_mode.value,
            "reason": case.expected_reason,
            "fresh": case.expected_fresh,
        }
        if actual != expected:
            failures.append(
                {"case_id": case.case_id, "expected": expected, "actual": actual}
            )
    passed = len(cases) - len(failures)
    return {
        "dataset": path.name,
        "router_version": INTENT_ROUTER_VERSION,
        "case_count": len(cases),
        "passed_cases": passed,
        "failed_cases": len(failures),
        "accuracy": round(passed / len(cases), 4),
        "expected_intent_counts": dict(sorted(expected_counts.items())),
        "failures": failures,
    }


def _profile() -> ProfileContext:
    return ProfileContext(
        user_id=UUID("00000000-0000-0000-0000-000000000001"),
        version=1,
        goal_type=GoalType.AGENT_APP,
        stage=CareerStage.PREPARING,
        time_budget_minutes=60,
        skill_level=SkillLevel.INTERMEDIATE,
    )
