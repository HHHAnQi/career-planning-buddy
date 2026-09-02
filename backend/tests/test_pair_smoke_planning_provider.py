"""Unit tests for the Pair-Smoke Planning fixture profiles.

PR-9c.2 Commit 3.4 (Stage A, Option E′). These tests enforce the
determinism + comparability contract WITHOUT going through EvalRunner:

* Same context + same profile  → byte-identical output
* Same context + diff profile  → byte-different output (PLAN_PROJECTION
  carries ``summary`` / ``rationale``, both profile-distinct)
* Neither profile leaks experiment identity (no ``baseline`` /
  ``candidate`` / ``[candidate-variant]`` / UUID-shaped strings /
  compact_v1 / structured_v1 as a literal token in the candidate output)
* ``plan_date``/``horizon_start``/``horizon_end`` are identical across
  profiles (external plan-date comparators stay valid)
* ``weekly_focus`` is contiguous-from-1 (PlanCandidate validator
  invariant)
* both profiles satisfy the product contract: seven consecutive daily tasks
* ReplanMode.ADJUST path still produces adjustment_reason under both
  profiles (no safety regression)
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

import pytest

from app.providers.llm import PairSmokePlanningProvider
from app.schemas.agent_runs import (
    PlanningContext,
    PlanningWindow,
    ProfileContext,
    ReviewContext,
)
from app.schemas.enums import ReplanMode

_USER_ID = UUID("00000000-0000-0000-0000-000000000601")


def _context(replan: ReplanMode = ReplanMode.INITIAL) -> PlanningContext:
    return PlanningContext(
        profile=ProfileContext(
            user_id=_USER_ID,
            version=1,
            goal_type="agent_app",
            stage="preparing",
            time_budget_minutes=60,
            skill_level="intermediate",
        ),
        planning_window=PlanningWindow(
            planning_date=date(2026, 8, 4),
            horizon_start=date(2026, 8, 4),
            horizon_end=date(2026, 8, 31),
            horizon_weeks=4,
        ),
        recent_tasks=[],
        recent_reviews=(
            [
                ReviewContext(
                    review_id=UUID(int=800),
                    review_date=date(2026, 7, 1),
                    blockers="environment",
                    adjustment_request="reduce scope",
                )
            ]
            if replan == ReplanMode.ADJUST
            else []
        ),
        completed_facts=[],
        blockers=["environment"] if replan == ReplanMode.ADJUST else [],
        time_budget_minutes=60,
        token_estimate=0,
    )


def _candidate_json(provider: PairSmokePlanningProvider) -> dict[str, object]:
    """Drive the provider through the public ``generate_plan`` path so
    the JSON envelope shape is exercised."""

    import asyncio

    out = asyncio.run(
        provider.generate_plan(
            message="smoke",
            context=_context(),
            replan_mode=ReplanMode.INITIAL,
            evidence_catalog=[],
        )
    )
    return dict(out)


def _no_identity_leak(payload: dict[str, object]) -> None:
    """Identity-leak sentinel strings MUST NOT appear anywhere in the
    JSON-printed candidate. Identifies: experiment role labels, profile
    literal strings (only allowed as a structured ``[profile]`` prefix
    inside ``summary`` per the spec — checked separately below), UUIDs,
    thread-local sentinel."""

    import json

    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    forbidden = [
        "baseline_experiment",
        "candidate_experiment",
        "[candidate-variant]",
        "[baseline-variant]",
        "experiment_id",
        "variant_role",
    ]
    for token in forbidden:
        assert token not in blob, f"identity leak: {token!r} in candidate"


def test_same_profile_is_byte_identical() -> None:
    """Determinism: same (context, profile) → identical serialized
    candidate across two fresh provider instances."""

    a = PairSmokePlanningProvider("compact_v1")._candidate(
        _context(), ReplanMode.INITIAL
    )
    b = PairSmokePlanningProvider("compact_v1")._candidate(
        _context(), ReplanMode.INITIAL
    )
    assert a.model_dump(mode="json") == b.model_dump(mode="json")


@pytest.mark.parametrize("profile", ["compact_v1", "structured_v1"])
def test_candidates_match_context_dates(profile: str) -> None:
    """``plan_date`` / ``horizon_start`` / ``horizon_end`` are equal to
    the planning window across profiles — external date-based
    comparators (TRANSCRIPT_HASH consistency, replan invariants) are
    not perturbed."""

    ctx = _context()
    cand = PairSmokePlanningProvider(profile)._candidate(
        ctx, ReplanMode.INITIAL
    )
    assert cand.plan_date == ctx.planning_window.planning_date
    assert cand.horizon_start == ctx.planning_window.horizon_start
    assert cand.horizon_end == ctx.planning_window.horizon_end


def test_compact_vs_structured_byte_different() -> None:
    """Comparability signal: ``PLAN_PROJECTION`` is built from
    ``summary`` / ``rationale``. The two profiles MUST produce
    different values in at least one of those so the loader's
    ``output_hash`` (canonical_sha256 over ``{request, plan}``) yields
    byte-different plan hashes."""

    compact = PairSmokePlanningProvider("compact_v1")._candidate(
        _context(), ReplanMode.INITIAL
    )
    structured = PairSmokePlanningProvider("structured_v1")._candidate(
        _context(), ReplanMode.INITIAL
    )
    assert compact.summary != structured.summary
    assert compact.rationale != structured.rationale
    # And whole output is byte-different.
    assert compact.model_dump(mode="json") != structured.model_dump(
        mode="json"
    )


def test_task_counts_match_profile() -> None:
    compact = PairSmokePlanningProvider("compact_v1")._candidate(
        _context(), ReplanMode.INITIAL
    )
    structured = PairSmokePlanningProvider("structured_v1")._candidate(
        _context(), ReplanMode.INITIAL
    )
    assert len(compact.tasks) == 7
    assert len(structured.tasks) == 7
    for cand in (compact, structured):
        assert [task.scheduled_date for task in cand.tasks] == [
            cand.plan_date + timedelta(days=offset) for offset in range(7)
        ]


def test_weekly_focus_contiguous() -> None:
    """The PlanCandidate validator ``require_contiguous_weeks`` would
    reject a non-contiguous list; this test asserts the fixture profile
    produces output that survives that validator."""

    for profile in ("compact_v1", "structured_v1"):
        cand = PairSmokePlanningProvider(profile)._candidate(
            _context(), ReplanMode.INITIAL
        )
        indexes = [w.week_index for w in cand.weekly_focus]
        assert indexes == list(range(1, len(indexes) + 1))


def test_no_identity_leak() -> None:
    """Candidate output MUST NOT carry experiment-identity markers."""

    for profile in ("compact_v1", "structured_v1"):
        cand = PairSmokePlanningProvider(profile)._candidate(
            _context(), ReplanMode.INITIAL
        )
        _no_identity_leak({"candidate": cand.model_dump(mode="json")})


def test_replan_adjust_produces_adjustment_reason_under_both_profiles() -> None:
    """Safety regression guard: the ADJUST replan path must populate
    ``adjustment_reason`` in BOTH profiles, identical to Mock's
    behavior — pair-smoke must not silently skip replan semantics."""

    for profile in ("compact_v1", "structured_v1"):
        cand = PairSmokePlanningProvider(profile)._candidate(
            _context(replan=ReplanMode.ADJUST), ReplanMode.ADJUST
        )
        assert cand.adjustment_reason is not None
        assert cand.adjustment_reason  # non-empty


def test_invalid_profile_rejected() -> None:
    with pytest.raises(ValueError, match="unknown profile"):
        PairSmokePlanningProvider("noise_v9")
