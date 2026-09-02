"""PR-8 counterfactual experiment dataset.

Grows the ``counterfactual-smoke-v1`` family: 4 paired groups covering
Memory / Context / Tool / Evidence ablation axes. Every group is small
(2-4 variants) and every pair within a group shares its base fixtures
(same user_request, same profile, same planning window) so a paired
diff can attribute any delta to the ablated axis alone.

Inline construction (mirroring ``evals.v2.runtime_smoke``) so the
``fixture_hash`` and ``source_sha256`` self-validate through
``canonical_sha256`` at load time without an on-disk JSONL file.

The dataset is intentionally compact: 10 cases total across 4 groups,
each variant executable through the full real Runtime in seconds.
"""

from __future__ import annotations

from evals.v2.contracts import (
    DatasetManifest,
    EvalCase,
    canonical_sha256,
)
from evals.v2.dataset_loader import DatasetBundle

DATASET_ID = "counterfactual-smoke"
DATASET_VERSION = "v1"
FIXTURE_VERSION = "counterfactual-smoke-v1"
PLANNING_DATE = "2026-08-01"

_BASE_PROFILE = {
    "goal_type": "skill_growth",
    "stage": "preparing",
    "time_budget_minutes": 120,
    "skill_level": "intermediate",
}

# All groups share the same baseline message so paired diff attribution
# is well-defined: any observed plan delta must trace back to the ablated
# axis alone.
_BASE_USER_REQUEST = "Help me plan the next 5 weeks of focused Python interviewing prep."

_MEMORY_CONSTRAINT = "每周总投入时长不得超过 6 小时"


def _build_scenario(
    *,
    user_request: str | None = None,
    profile: dict[str, object] | None = None,
    initial_tasks: list[dict[str, object]] | None = None,
    confirmed_memories: list[dict[str, object]] | None = None,
    provider_fixtures: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "user_request": user_request or _BASE_USER_REQUEST,
        "profile": profile or _BASE_PROFILE,
        "hint_intent": "create_plan",
        "replan_mode": None,
        "initial_plan": None,
        "initial_tasks": initial_tasks or [],
        "initial_reviews": [],
        "confirmed_memories": confirmed_memories or [],
        "unconfirmed_memory_candidates": [],
        "search_fixtures": {},
        "provider_fixtures": provider_fixtures or {},
        "planning_date": PLANNING_DATE,
    }


def _case(
    case_id: str,
    *,
    variant: str,
    counterfactual_group_id: str,
    scenario: dict[str, object],
    expected_outcome: dict[str, object] | None = None,
    trajectory_policy: dict[str, object] | None = None,
    rubric_criteria: list[dict[str, object]] | None = None,
    tags: list[str] | None = None,
    fault_plan: dict[str, object] | None = None,
) -> EvalCase:
    payload: dict[str, object] = {
        "case_id": case_id,
        "schema_version": "2",
        "dataset_id": DATASET_ID,
        "dataset_version": DATASET_VERSION,
        "scenario": scenario,
        "expected_outcome": expected_outcome
        or {
            "result_kind": "plan",
            "allowed_run_statuses": ["completed"],
        },
        "trajectory_policy": trajectory_policy
        or {
            "expected_tools": [],
            "max_tool_calls": 4,
            "require_terminal_event": True,
        },
        "rubric": {
            "criteria": rubric_criteria
            or [
                {
                    "criterion_id": "allowed_run_status",
                    "description": "Run terminates in one of the versioned allowed statuses.",
                    "hard_gate": True,
                }
            ]
        },
        "difficulty": "capability",
        "tags": tags or ["counterfactual"],
        "fixture_version": FIXTURE_VERSION,
        "counterfactual_group_id": counterfactual_group_id,
        "variant": variant,
        "fault_plan": fault_plan,
    }
    payload["fixture_hash"] = canonical_sha256(
        {k: v for k, v in payload.items() if k != "fixture_hash"}
    )
    return EvalCase.model_validate(payload)


# ---------------------------------------------------------------------------
# Group cf-mem-01: Memory ablation (4 variants)
# ---------------------------------------------------------------------------


def _memory_cases() -> list[EvalCase]:
    """4 paired variants exercising Memory category ablation.

    All seed an identical profile + user_request and target the same
    constraint (``provider_fixtures.expect_constraint``). The Memory rows
    that get planted differ by ``category``:

    * ``no_memory`` -- baseline, no planted Memory.
    * ``relevant_memory`` -- plants a Memory whose content reinforces the
      constraint (``category='relevant'``).
    * ``irrelevant_memory`` -- plants an unrelated Memory
      (``category='irrelevant'``).
    * ``conflicting_memory`` -- plants a Memory whose content contradicts
      the constraint (``category='conflicting'``).
    """

    group = "cf-mem-01"
    base_provider_fixtures: dict[str, object] = {
        "memory_axis": True,
        "expect_constraint": _MEMORY_CONSTRAINT,
        # All variants share the same constraint expressed verbatim in
        # the user request so the runtime has at least one authoritative
        # source even when Memory is absent.
    }

    variants: list[tuple[str, list[dict[str, object]]]] = [
        ("no_memory", []),
        (
            "relevant_memory",
            [
                {
                    "content": f"User explicitly requested: {_MEMORY_CONSTRAINT}",
                    "category": "relevant",
                    "memory_id": "mem-rel-01",
                }
            ],
        ),
        (
            "irrelevant_memory",
            [
                {
                    "content": "User owns a vintage Schwinn bicycle.",
                    "category": "irrelevant",
                    "memory_id": "mem-irr-01",
                }
            ],
        ),
        (
            "conflicting_memory",
            [
                {
                    "content": "User prefers to overcommit and is happy with 20h+ weeks.",
                    "category": "conflicting",
                    "memory_id": "mem-con-01",
                }
            ],
        ),
    ]

    cases: list[EvalCase] = []
    for variant, mems in variants:
        scenario = _build_scenario(
            user_request=_BASE_USER_REQUEST + f" ({_MEMORY_CONSTRAINT})",
            confirmed_memories=mems,
            provider_fixtures=dict(base_provider_fixtures),
        )
        cases.append(
            _case(
                case_id=f"cf-mem-01-{variant}",
                variant=variant,
                counterfactual_group_id=group,
                scenario=scenario,
                tags=["counterfactual", "memory"],
            )
        )
    return cases


# ---------------------------------------------------------------------------
# Group cf-ctx-01: Context compression ablation (2 variants)
# ---------------------------------------------------------------------------


def _context_cases() -> list[EvalCase]:
    """Two variants exercising compression budgets.

    Variant ``full_context`` uses defaults (recent_tasks=5,
    recent_reviews=2); ``compressed_context`` tightens them (2 and 0).
    Both share the same source plan + recent tasks so the only delta is
    the budget.
    """

    group = "cf-ctx-01"
    base_recent_tasks = [
        {
            "task_id": f"ctx-task-{i}",
            "title": f"prior task {i}",
            "task_type": "learning",
            "scheduled_date": "2026-07-2" + str(i % 7),
            "deliverable": "x",
            "starter_actions": "y",
            "estimated_minutes": 30,
            "state": "completed",
        }
        for i in range(6)
    ]
    variants = [
        ("full_context", {"recent_tasks_budget": 5, "recent_reviews_budget": 2}),
        ("compressed_context", {"recent_tasks_budget": 2, "recent_reviews_budget": 0}),
    ]
    cases: list[EvalCase] = []
    for variant, budget in variants:
        scenario = _build_scenario(
            initial_tasks=base_recent_tasks,
            provider_fixtures={"context_compression": budget},
        )
        cases.append(
            _case(
                case_id=f"cf-ctx-01-{variant}",
                variant=variant,
                counterfactual_group_id=group,
                scenario=scenario,
                tags=["counterfactual", "context"],
            )
        )
    return cases


# ---------------------------------------------------------------------------
# Group cf-tool-01: Tool availability ablation (2 variants)
# ---------------------------------------------------------------------------


def _tool_cases() -> list[EvalCase]:
    """Two variants exercising the per-case Tool allowlist.

    The case is explicitly ``tool_required`` (a memory_lookup must be
    attempted) so the ``tool_available`` arm produces a successful call
    and the ``tool_unavailable`` arm produces no successful call.
    """

    group = "cf-tool-01"
    common_policy = {
        "expected_tools": ["memory_lookup"],
        "max_tool_calls": 2,
        "require_terminal_event": True,
    }
    variants = [
        ("tool_available", ["memory_lookup", "rag_retrieve", "web_search"]),
        ("tool_unavailable", []),
    ]
    cases: list[EvalCase] = []
    for variant, tools in variants:
        scenario = _build_scenario(
            provider_fixtures={
                "available_tools": tools,
                "tool_required": True,
                "expect_tool": "memory_lookup",
            }
        )
        cases.append(
            _case(
                case_id=f"cf-tool-01-{variant}",
                variant=variant,
                counterfactual_group_id=group,
                scenario=scenario,
                trajectory_policy=common_policy,
                tags=["counterfactual", "tool"],
            )
        )
    return cases


# ---------------------------------------------------------------------------
# Group cf-evi-01: Evidence citation ablation (2 variants)
# ---------------------------------------------------------------------------


def _evidence_cases() -> list[EvalCase]:
    """Two variants exercising Evidence citation.

    Both plant the same expected citations ("mem-A", "mem-B"); variants
    differ in whether those memories are visible in the catalog or
    hidden. The grader compares ``plan.evidence_refs`` against the
    expected set.
    """

    group = "cf-evi-01"
    expected = ["mem-A", "mem-B"]
    variants = [
        ("visible_evidence", "visible"),
        ("hidden_evidence", "hidden"),
    ]
    cases: list[EvalCase] = []
    for variant, visibility in variants:
        scenario = _build_scenario(
            confirmed_memories=[
                {
                    "content": "Memory A: prefers short focused sessions.",
                    "category": "relevant",
                    "memory_id": "mem-A",
                },
                {
                    "content": "Memory B: dislikes evenings.",
                    "category": "relevant",
                    "memory_id": "mem-B",
                },
            ],
            provider_fixtures={
                "expected_citations": expected,
                "pinned_memory_visibility": visibility,
                "evidence_axis": True,
            },
        )
        cases.append(
            _case(
                case_id=f"cf-evi-01-{variant}",
                variant=variant,
                counterfactual_group_id=group,
                scenario=scenario,
                tags=["counterfactual", "evidence"],
            )
        )
    return cases


def load_counterfactual_dataset() -> DatasetBundle:
    """Return the versioned 10-case counterfactual smoke dataset."""

    cases = (
        _memory_cases()
        + _context_cases()
        + _tool_cases()
        + _evidence_cases()
    )
    source = canonical_sha256([case.fixture_payload() for case in cases])
    manifest = DatasetManifest(
        manifest_version="2",
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        case_schema_version="2",
        source_path="datasets/counterfactual-smoke-v1.jsonl",
        source_format="eval_case_v2_jsonl",
        source_sha256=source,
        case_count=len(cases),
    )
    return DatasetBundle(manifest=manifest, cases=cases)
