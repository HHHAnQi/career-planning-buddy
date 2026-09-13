"""Frozen V2 ``goal_type`` mapping stability tests.

PR-3 prerequisite (revision #5): the FixtureLoader must never guess the
``EvalProfile.goal_type`` → Runtime ``GoalType`` mapping at runtime. The table
is frozen and immutable; every Stage 5 case must resolve deterministically.
"""

from types import MappingProxyType
from uuid import UUID

import pytest

from app.schemas.enums import CareerStage, GoalType, SkillLevel
from evals.v2.contracts import EvalScenario
from evals.v2.dataset_loader import load_dataset
from evals.v2.profile_mapping import (
    EVAL_GOAL_TYPE_TO_RUNTIME,
    MAPPING_VERSION,
    map_goal_type,
    scenario_to_profile_payload,
)


def test_mapping_is_immutable_and_versioned() -> None:
    assert isinstance(EVAL_GOAL_TYPE_TO_RUNTIME, MappingProxyType)
    assert MAPPING_VERSION.startswith("goal-type-mapping-")
    # Every Stage 5 goal_type literal is covered.
    expected_keys = {"job_search", "internship", "career_change", "skill_growth"}
    assert set(EVAL_GOAL_TYPE_TO_RUNTIME) == expected_keys
    # Mapped values are real Runtime GoalType members.
    for value in EVAL_GOAL_TYPE_TO_RUNTIME.values():
        assert isinstance(value, GoalType)
    # No two literals collapse to the same runtime goal (intentional separation).
    assert len(set(EVAL_GOAL_TYPE_TO_RUNTIME.values())) == len(EVAL_GOAL_TYPE_TO_RUNTIME)


def test_unmapped_goal_type_raises_rather_than_guessing() -> None:
    with pytest.raises(ValueError, match="no frozen goal_type mapping"):
        map_goal_type("nonexistent_goal")


def test_every_stage5_case_profile_resolves_to_runtime_payload() -> None:
    bundle = load_dataset()
    assert len(bundle.cases) == 33  # 30 mock + 3 live-only
    for case in bundle.cases:
        profile = case.scenario.profile
        if profile is None:  # clarify-* cases intentionally omit the profile
            continue
        payload = scenario_to_profile_payload(case.scenario)
        assert isinstance(payload.goal_type, GoalType)
        assert isinstance(payload.stage, CareerStage)
        assert isinstance(payload.skill_level, SkillLevel)
        assert 15 <= payload.time_budget_minutes <= 480
        # The mapping is the sole authority: the source literal must be in the table.
        assert profile.goal_type in EVAL_GOAL_TYPE_TO_RUNTIME
        assert payload.goal_type is EVAL_GOAL_TYPE_TO_RUNTIME[profile.goal_type]


def test_clarify_cases_without_profile_raise_on_payload_build() -> None:
    scenario = EvalScenario(
        user_request="Create a career plan",
        profile=None,
        hint_intent=None,
        replan_mode=None,
        initial_plan=None,
        initial_tasks=[],
        initial_reviews=[],
        confirmed_memories=[],
        unconfirmed_memory_candidates=[],
        search_fixtures={},
        provider_fixtures={},
        planning_date="2026-08-01",
    )
    with pytest.raises(ValueError, match="no profile to map"):
        scenario_to_profile_payload(scenario)


def test_forbidden_member_goal_type_agent_app_is_never_a_target() -> None:
    # The Runtime has goals like AGENT_APP that MUST NOT be the mapping target of
    # a V2 eval literal other than the one explicitly assigned. Pin the contract.
    assert GoalType.AGENT_APP is EVAL_GOAL_TYPE_TO_RUNTIME["internship"]
    assert EVAL_GOAL_TYPE_TO_RUNTIME["job_search"] is not GoalType.AGENT_APP


# Suppress unused-import linting noise for UUID (kept to demonstrate that no
# runtime id is needed for a deterministic mapping).
_: UUID = UUID(int=0)
