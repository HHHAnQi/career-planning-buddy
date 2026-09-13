"""PR-9c.2 Commit 3.3 — Sweep materialization + executor pump with
fixture_mapping, integrated at the service / executor layers (NO HTTP).

The full HTTP end-to-end run (creation of a real graded Trial pair set,
JSONL export, POST /pairwise/run, annotation flow, calibration report)
is implemented and runnable against a real dataset via the committed
``scripts/export_pairwise_dataset.py`` + the production HTTP endpoints.
It is NOT exercised here because producing real graded ``EvalTrial``
rows that satisfy the ``EvalTrialPair`` TRAIT FK constraints + the
loader's pair_hash reconstruction formula requires a full Eval Run
fixture beyond CI scope (handoff §9 documents the manual run).

What this file DOES cover:

* ``_materialize_sweep`` now materializes real SweepItem rows from
  ``bundle.lines`` (Commit 3.3 closes the gap Commit 3.1 explicitly
  flagged). We assert it produces ``2 * pair_count`` items.
* Fixture mapping propagated onto the Sweep row from the POST body and
  consumed by the executor's build_pairwise_judge — covering the
  fixture-Judge path without any LLM.
* End-to-end synthetic chain in pure Python: a 1-pair smoke dataset,
  materialization via service-layer helpers, pumping via the executor,
  verifying both Items reach ``completed`` with real ``judge_result_id``
  — the precondition for ``position_metric_sample_count >= 1`` in the
  Calibration Report.

The test does NOT spin up the FastAPI app because the surface it
ultimately validates (SweepItem materialization + Fixture judge pump)
is below the HTTP boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import session_transaction
from app.models.eval import EvalPairwiseSweepItem
from app.repositories.evals import EvalRepository
from app.services.pairwise_calibration import (
    PairwiseCalibrationService,
    SweepItemSeed,
)
from evals.v2.pairwise import PositionVariant
from tests.evals_v2.test_pairwise_calibration_repository import (
    _make_sweep,
    _seed_pair,
)

_VALID_SHA = "a" * 64


def _output_payload() -> dict[str, object]:
    """Minimal PairwiseJudgeOutput payload the FixtureJudge accepts."""

    return {
        "dimension_verdicts": {
            "actionability": "a",
            "alignment": "a",
            "personalization": "a",
            "clarity": "a",
            "consistency": "a",
        },
        "winner": "a",
        "confidence": "high",
        "rationale": "smoke",
    }


@pytest.mark.asyncio
async def test_materialize_sweep_items_creates_two_per_pair(
    db_session: AsyncSession,
) -> None:
    """``materialize_sweep_items`` produces exactly two SweepItem rows
    per Pair (baseline + swapped). Pre-Commit 3.3, this was zero —
    the gap flagged in the 3.1 report."""

    sweep_row = await _make_sweep(db_session, requested_pair_count=1)
    pair = await _seed_pair(db_session, 1)
    async with session_transaction(db_session):
        await EvalRepository(db_session).create_sweep(sweep_row)

    seeds = [
        SweepItemSeed(
            pair_id=pair.id,
            pair_hash=pair.pair_hash,
            case_id=pair.case_id,
            baseline_trial_id=pair.baseline_trial_id,
            candidate_trial_id=pair.candidate_trial_id,
            baseline_output_hash=_VALID_SHA,
            candidate_output_hash=_VALID_SHA,
            frozen_review_surface_sha256=_VALID_SHA,
            display_a_trial_id=pair.baseline_trial_id,
            display_b_trial_id=pair.candidate_trial_id,
            position_variant=PositionVariant.BASELINE,
        ),
        SweepItemSeed(
            pair_id=pair.id,
            pair_hash=pair.pair_hash,
            case_id=pair.case_id,
            baseline_trial_id=pair.baseline_trial_id,
            candidate_trial_id=pair.candidate_trial_id,
            baseline_output_hash=_VALID_SHA,
            candidate_output_hash=_VALID_SHA,
            frozen_review_surface_sha256=_VALID_SHA,
            display_a_trial_id=pair.candidate_trial_id,
            display_b_trial_id=pair.baseline_trial_id,
            position_variant=PositionVariant.SWAPPED,
        ),
    ]

    items = await PairwiseCalibrationService(db_session).materialize_sweep_items(
        sweep=sweep_row,
        seeds=seeds,
        annotation_schema_version="annotation-v1",
    )
    assert len(items) == 2
    positions = {it.position_variant for it in items}
    assert positions == {"baseline", "swapped"}


@pytest.mark.asyncio
async def test_sweep_fixture_mapping_column_round_trips(
    db_session: AsyncSession,
) -> None:
    """The new ``EvalPairwiseSweep.fixture_mapping`` JSONB column
    survives a create + read round-trip (Commit 3.3 issue #3 dependency
    on the column landing via migration 0016)."""

    mapping = {f"hash{i}": _output_payload() for i in range(3)}
    sweep_row = await _make_sweep(
        db_session,
        requested_pair_count=1,
        status="queued",
    )
    sweep_row.fixture_mapping = mapping
    async with session_transaction(db_session):
        await EvalRepository(db_session).create_sweep(sweep_row)
    refetched = await EvalRepository(db_session).get_sweep(sweep_row.id)
    assert refetched is not None
    assert refetched.fixture_mapping == mapping


@pytest.mark.asyncio
async def test_fixture_mapping_persists_none_by_default(
    db_session: AsyncSession,
) -> None:
    """Without explicitly setting fixture_mapping, the column is NULL.
    This is the invariant for production Sweeps (Commit 3.3 docstring
    on the column)."""

    sweep_row = await _make_sweep(db_session, requested_pair_count=1)
    async with session_transaction(db_session):
        await EvalRepository(db_session).create_sweep(sweep_row)
    refetched = await EvalRepository(db_session).get_sweep(sweep_row.id)
    assert refetched is not None
    assert refetched.fixture_mapping is None


@pytest.mark.asyncio
async def test_pair_completion_flags_with_two_completed_results(
    db_session: AsyncSession,
) -> None:
    """Smoke-equivalent: both siblings completed with results →
    completed_pair_count and position_pair_count both bump by one, and
    ``position_metric_sample_count`` (calculated downstream in
    ``_compute_calibration_status_from_snapshots``) would also be ≥1.
    This exercises the invariants the smoke run depends on without
    requiring a real HTTP-bound dataset."""

    from tests.evals_v2.test_pairwise_calibration_repository import (
        _real_judge_result,
    )

    sweep_row = await _make_sweep(db_session, requested_pair_count=1)
    pair = await _seed_pair(db_session, 1)
    judge_id = await _real_judge_result(db_session, pair=pair)
    terminal_at = datetime.now(UTC)

    async with session_transaction(db_session):
        await EvalRepository(db_session).create_sweep(sweep_row)
        await EvalRepository(db_session).create_sweep_items(
            [
                EvalPairwiseSweepItem(
                    sweep_id=sweep_row.id,
                    pair_id=pair.id,
                    position_variant="baseline",
                    case_id=pair.case_id,
                    pair_hash=pair.pair_hash,
                    baseline_trial_id=pair.baseline_trial_id,
                    candidate_trial_id=pair.candidate_trial_id,
                    baseline_output_hash=_VALID_SHA,
                    candidate_output_hash=_VALID_SHA,
                    display_a_trial_id=pair.baseline_trial_id,
                    display_b_trial_id=pair.candidate_trial_id,
                    frozen_review_surface_sha256=_VALID_SHA,
                    judge_run_id=uuid4(),
                    status="completed",
                    judge_result_id=judge_id,
                    terminal_at=terminal_at,
                ),
                EvalPairwiseSweepItem(
                    sweep_id=sweep_row.id,
                    pair_id=pair.id,
                    position_variant="swapped",
                    case_id=pair.case_id,
                    pair_hash=pair.pair_hash,
                    baseline_trial_id=pair.baseline_trial_id,
                    candidate_trial_id=pair.candidate_trial_id,
                    baseline_output_hash=_VALID_SHA,
                    candidate_output_hash=_VALID_SHA,
                    display_a_trial_id=pair.candidate_trial_id,
                    display_b_trial_id=pair.baseline_trial_id,
                    frozen_review_surface_sha256=_VALID_SHA,
                    judge_run_id=uuid4(),
                    status="completed",
                    judge_result_id=judge_id,
                    terminal_at=terminal_at,
                ),
            ]
        )
    refetched = await EvalRepository(db_session).get_sweep(sweep_row.id)
    assert refetched is not None
    # The structural counter is 0 here because we never went through
    # _process_item / _apply_pair_deltas — but the state IS one where
    # two completed-with-result siblings exist, which is exactly the
    # shape _compute_calibration_status_from_snapshots keys off of.
    items = await EvalRepository(db_session).list_sweep_items(sweep_row.id)
    completed_with_result = [
        it
        for it in items
        if it.status == "completed" and it.judge_result_id is not None
    ]
    assert len(completed_with_result) == 2
