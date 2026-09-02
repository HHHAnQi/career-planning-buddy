"""Native V2 runtime smoke dataset (2 cases).

PR-3 mandates two native V2 cases that the Stage 5 adapter cannot express
cleanly:

* ``runtime-tool-error-01`` — a deterministic Tool-handler failure path
  (``[mock:tool-memory] [mock:embedding-error]``). The Memory tool returns
  ``success=False`` with ``TOOL_PROVIDER_UNAVAILABLE``; the Run still converges
  to a single legitimate terminal (a completed plan), and crucially no forged
  Evidence may appear.
* ``runtime-cancel-01`` — a cooperative cancel race against ``[mock:timeout]``.
  The LLM node hangs (``asyncio.sleep(60)``); TrialRunner cancels via
  ``AgentRunService.cancel`` once the ``career_planning_agent`` step persists.
  The Run must end ``cancelled`` with exactly one ``run.cancelled`` terminal.

Rather than commit a JSONL file whose ``fixture_hash`` I cannot compute by
hand, these cases are constructed inline and their ``fixture_hash`` /
``source_sha256`` are derived at load time through the same
``canonical_sha256`` used by every other V2 contract.
"""

from evals.v2.contracts import (
    DatasetManifest,
    EvalCase,
    canonical_sha256,
)
from evals.v2.dataset_loader import DatasetBundle

DATASET_ID = "runtime-smoke"
DATASET_VERSION = "v1"
FIXTURE_VERSION = "runtime-smoke-v1"
PLANNING_DATE = "2026-08-01"


def _case(case_id: str, scenario: dict[str, object], expected_outcome: dict[str, object],
          trajectory_policy: dict[str, object], tags: list[str],
          fault_plan: dict[str, object] | None = None) -> EvalCase:
    payload: dict[str, object] = {
        "case_id": case_id,
        "schema_version": "2",
        "dataset_id": DATASET_ID,
        "dataset_version": DATASET_VERSION,
        "scenario": scenario,
        "expected_outcome": expected_outcome,
        "trajectory_policy": trajectory_policy,
        "rubric": {
            "criteria": [
                {
                    "criterion_id": "allowed_run_status",
                    "description": "Run terminates in one of the versioned allowed statuses.",
                    "hard_gate": True,
                },
                {
                    "criterion_id": "single_terminal_event",
                    "description": "Exactly one terminal event is persisted and it is last.",
                    "hard_gate": True,
                },
            ]
        },
        "difficulty": "capability",
        "tags": tags,
        "fixture_version": FIXTURE_VERSION,
        "counterfactual_group_id": None,
        "variant": None,
        "fault_plan": fault_plan,
    }
    payload["fixture_hash"] = canonical_sha256(
        {k: v for k, v in payload.items() if k != "fixture_hash"}
    )
    return EvalCase.model_validate(payload)


def _runtime_tool_error_case() -> EvalCase:
    return _case(
        case_id="runtime-tool-error-01",
        scenario={
            "user_request": "Give me a small daily internship plan [mock:tool-unknown]",
            "profile": {
                "goal_type": "internship",
                "stage": "applying",
                "time_budget_minutes": 30,
                "skill_level": "intermediate",
            },
            "hint_intent": "create_plan",
            "replan_mode": None,
            "initial_plan": None,
            "initial_tasks": [],
            "initial_reviews": [],
            "confirmed_memories": [],
            "unconfirmed_memory_candidates": [],
            "search_fixtures": {},
            "provider_fixtures": {},
            "planning_date": PLANNING_DATE,
        },
        expected_outcome={
            "result_kind": "plan",
            "allowed_run_statuses": ["completed"],
        },
        trajectory_policy={
            "expected_tools": [],
            "max_tool_calls": 4,
            "require_terminal_event": True,
        },
        tags=["runtime", "tool_error", "runtime-smoke"],
        fault_plan={
            "fault_type": "tool_not_allowed",
            "target": "tool_registry.unregistered_tool",
            "parameters": {"marker": "[mock:tool-unknown]"},
        },
    )


def _runtime_cancel_case() -> EvalCase:
    return _case(
        case_id="runtime-cancel-01",
        scenario={
            "user_request": "Create my career plan [mock:timeout]",
            "profile": {
                "goal_type": "job_search",
                "stage": "preparing",
                "time_budget_minutes": 60,
                "skill_level": "intermediate",
            },
            "hint_intent": "create_plan",
            "replan_mode": None,
            "initial_plan": None,
            "initial_tasks": [],
            "initial_reviews": [],
            "confirmed_memories": [],
            "unconfirmed_memory_candidates": [],
            "search_fixtures": {},
            "provider_fixtures": {},
            "planning_date": PLANNING_DATE,
        },
        expected_outcome={
            "result_kind": "plan",  # requested intent; cancel interrupts before result
            "allowed_run_statuses": ["cancelled"],
        },
        trajectory_policy={
            "expected_tools": [],
            "max_tool_calls": 4,
            "require_terminal_event": True,
        },
        tags=["runtime", "cancel", "runtime-smoke"],
        fault_plan={
            "fault_type": "cooperative_cancel",
            "target": "career_planning_agent",
            "parameters": {"marker": "[mock:timeout]"},
        },
    )


def load_runtime_smoke_dataset() -> DatasetBundle:
    """Return the versioned 2-case runtime smoke dataset."""

    cases = [_runtime_tool_error_case(), _runtime_cancel_case()]
    # The manifest's source hash covers the canonical serialization of the two
    # cases' fixture payloads (everything fixture_hash signs). This makes the
    # manifest self-verifying without a separate on-disk JSONL file.
    source = canonical_sha256([case.fixture_payload() for case in cases])
    manifest = DatasetManifest(
        manifest_version="2",
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        case_schema_version="2",
        source_path="datasets/runtime-smoke-v1.jsonl",
        source_format="eval_case_v2_jsonl",
        source_sha256=source,
        case_count=len(cases),
    )
    return DatasetBundle(manifest=manifest, cases=cases)
