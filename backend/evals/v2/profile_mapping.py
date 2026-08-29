"""Frozen mapping from V2 ``EvalProfile`` goal vocabulary to Runtime ``GoalType``.

The Stage 5 dataset and the V2 ``EvalContract`` ``EvalProfile`` use a generic
process-style ``goal_type`` literal (``job_search`` / ``internship`` /
``career_change`` / ``skill_growth``). The real Runtime ``GoalType`` enum is a
closed set of concrete career directions. They share NO common value, so a
direct ``GoalType(profile.goal_type)`` would raise ``ValueError``.

PR-3 must not let the FixtureLoader guess at runtime. This module exposes a
single immutable, versioned mapping table plus a helper that produces a fully
populated ``ProfilePutRequest`` from an ``EvalScenario``. ``stage`` and
``skill_level`` are already value-compatible with the Runtime enums and are
passed through unchanged.
"""

from datetime import timedelta
from types import MappingProxyType

from app.core.time import product_today
from app.schemas.enums import CareerStage, GoalType, SkillLevel
from app.schemas.profile import ProfilePutRequest
from evals.v2.contracts import EvalScenarioUnion, PlanningEvalScenario

MAPPING_VERSION = "goal-type-mapping-v1"

EVAL_GOAL_TYPE_TO_RUNTIME: MappingProxyType[str, GoalType] = MappingProxyType(
    {
        "job_search": GoalType.BACKEND_JAVA,
        "internship": GoalType.AGENT_APP,
        "career_change": GoalType.AI_BACKEND,
        "skill_growth": GoalType.FULLSTACK,
    }
)


def map_goal_type(eval_goal_type: str) -> GoalType:
    """Return the frozen Runtime ``GoalType`` for a V2 ``goal_type`` literal."""

    try:
        return EVAL_GOAL_TYPE_TO_RUNTIME[eval_goal_type]
    except KeyError as exc:
        raise ValueError(
            "no frozen goal_type mapping for "
            f"{eval_goal_type!r}; update {MAPPING_VERSION} explicitly"
        ) from exc


def scenario_to_profile_payload(scenario: EvalScenarioUnion) -> ProfilePutRequest:
    """Build a Runtime ``ProfilePutRequest`` from a V2 ``EvalScenario``.

    The V2 Adapter guarantees ``stage`` / ``skill_level`` literals share their
    value with the Runtime enum, so they are passed through directly. Only
    ``goal_type`` requires the explicit frozen mapping.
    """

    if not isinstance(scenario, PlanningEvalScenario) or scenario.profile is None:
        raise ValueError("scenario has no profile to map")
    profile = scenario.profile
    return ProfilePutRequest(
        goal_type=map_goal_type(profile.goal_type),
        stage=CareerStage(profile.stage),
        time_budget_minutes=profile.time_budget_minutes,
        skill_level=SkillLevel(profile.skill_level),
        skill_summary=f"eval-profile:{profile.goal_type}",
        start_date=product_today(),
        deadline=product_today() + timedelta(days=27),
    )
