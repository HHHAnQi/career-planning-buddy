"""Map a V2 ``EvalScenario`` onto real Runtime creation services.

The Runtime exposes two legitimate replan entry points:

* ``hint_intent="replan"`` + ``source_plan_id`` → ``AgentRunService.create``
  drives a ``continue`` replan directly.
* An adjustment Review seeded by the FixtureLoader →
  ``ReviewService.start_next_plan`` drives an ``adjust`` replan and stamps
  ``replan_mode="adjust"`` on the new Run.

This module returns a small ``RuntimeLaunch`` value object describing which
service to call and with which arguments. The TrialRunner owns the actual
service invocation and terminal wait so that no Runtime logic leaks into the
eval layer.
"""

from dataclasses import dataclass
from uuid import UUID

from evals.v2.contracts import EvalScenario


@dataclass(frozen=True, slots=True)
class RuntimeLaunch:
    """How the TrialRunner should create the Run for one Trial."""

    kind: str  # "create" | "review_start_next"
    message: str
    hint_intent: str | None
    source_plan_id: UUID | None
    review_id: UUID | None
    idempotency_suffix: str


def adapt_scenario(
    scenario: EvalScenario,
    *,
    trial_id: UUID,
    source_plan_id: UUID | None,
    review_id: UUID | None,
) -> RuntimeLaunch:
    """Resolve the Runtime launch parameters for one Trial.

    ``source_plan_id`` / ``review_id`` are produced by the FixtureLoader when
    the scenario is a replan; for ``create_plan`` they stay ``None`` and the
    intent flows straight through to ``AgentRunService.create``.
    """

    base_key = f"trial-{trial_id}"
    if scenario.hint_intent == "replan":
        if scenario.replan_mode == "adjust":
            if review_id is None:
                raise ValueError(
                    "adjust replan scenario requires a seeded Review id"
                )
            return RuntimeLaunch(
                kind="review_start_next",
                message=scenario.user_request,
                hint_intent="replan",
                source_plan_id=source_plan_id,
                review_id=review_id,
                idempotency_suffix=f"{base_key}-adjust",
            )
        # continue
        if source_plan_id is None:
            raise ValueError(
                "continue replan scenario requires a seeded source Plan id"
            )
        return RuntimeLaunch(
            kind="create",
            message=scenario.user_request,
            hint_intent="replan",
            source_plan_id=source_plan_id,
            review_id=None,
            idempotency_suffix=f"{base_key}-continue",
        )
    return RuntimeLaunch(
        kind="create",
        message=scenario.user_request,
        hint_intent=scenario.hint_intent,
        source_plan_id=None,
        review_id=None,
        idempotency_suffix=base_key,
    )
