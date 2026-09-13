"""PR-9c.2 Pairwise Calibration service tests.

Focuses on transactional / contract behaviour that the repository tests
DO NOT cover:

* Annotation submit idempotency vs payload conflict (200 vs 409)
* Primary reviewer limit (third primary → 409 primary_full)
* Adjudicator must be a different reviewer from the two primaries (409)
* Adjudication precondition: exactly two primaries + any disagreement
  (overall OR any one dimension) — primary-only unanimous → 409
* Calibration report same input + same content → existing; same input +
  different content → integrity error
* Cancel request sets only cancel_requested_at; status stays running
"""

from __future__ import annotations

from typing import TypedDict
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import session_transaction
from app.models.eval import (
    EvalPairwiseHumanAnnotation,
    EvalPairwiseSweep,
    EvalTrialPair,
)
from app.repositories.evals import EvalRepository
from app.services.pairwise_calibration import (
    AnnotationSubmission,
    PairwiseCalibrationError,
    PairwiseCalibrationService,
    SweepItemSeed,
)
from evals.v2.contracts import canonical_sha256
from evals.v2.pairwise import JUDGE_ALLOWED_KINDS, PositionVariant
from tests.evals_v2.test_eval_repository import _config
from tests.evals_v2.test_pairwise_calibration_repository import (
    _make_sweep as _repo_make_sweep,
)
from tests.evals_v2.test_pairwise_calibration_repository import _seed_pair

_VALID_SHA = "a" * 64


async def _setup_sweep(
    db_session: AsyncSession, *, requested_pair_count: int = 1
) -> tuple[EvalPairwiseSweep, EvalTrialPair]:
    sweep = await _repo_make_sweep(
        db_session, requested_pair_count=requested_pair_count, status="queued"
    )
    async with session_transaction(db_session):
        await EvalRepository(db_session).create_sweep(sweep)
        await EvalRepository(db_session).mark_sweep_running(sweep.id)
    pair = await _seed_pair(db_session, 1)
    return sweep, pair


_DIM_NAMES = ("actionability", "alignment", "personalization", "clarity", "consistency")


def _raw_dims(val: str = "a") -> dict[str, str]:
    return {d: val for d in _DIM_NAMES}


def _norm_dims(val: str = "baseline") -> dict[str, str]:
    return {d: val for d in _DIM_NAMES}


# ===========================================================================
# Annotation submit: idempotency + conflict
# ===========================================================================


def _submission(
    *,
    pair_id: UUID,
    sweep_id: UUID,
    reviewer_id: str,
    raw_winner: str = "a",
    raw_dim_val: str = "a",
    norm_winner: str = "baseline",
    norm_dim_val: str = "baseline",
    is_adjudication: bool = False,
    rationale: str | None = "ok",
) -> AnnotationSubmission:
    return AnnotationSubmission(
        pair_id=pair_id,
        sweep_id=sweep_id,
        reviewer_id=reviewer_id,
        raw_winner=raw_winner,
        raw_dimension_verdicts=_raw_dims(raw_dim_val),
        normalized_winner=norm_winner,
        normalized_dimension_verdicts=_norm_dims(norm_dim_val),
        rationale=rationale,
        is_adjudication=is_adjudication,
    )


class _AnnSubmitContext(TypedDict):
    """Common kwargs passed to submit_annotation / submit_adjudication.

    Using a TypedDict lets us ``**``-spread the same context across calls
    without mypy complaining about the heterogeneous value types.
    """

    dataset_id: str
    dataset_version: str
    annotation_schema_version: str
    rubric_version: str
    judge_prompt_version: str
    judge_model_id: str
    frozen_review_surface_sha256: str
    position_variant: PositionVariant


_COMMON_ANN_KWARGS: _AnnSubmitContext = {
    "dataset_id": "ds",
    "dataset_version": "v1",
    "annotation_schema_version": "v1",
    "rubric_version": "v1",
    "judge_prompt_version": "v1",
    "judge_model_id": "judge-m1",
    "frozen_review_surface_sha256": _VALID_SHA,
    "position_variant": PositionVariant.BASELINE,
}


@pytest.mark.asyncio
async def test_annotation_idempotent_replay_returns_existing(
    db_session: AsyncSession,
) -> None:
    sweep, pair = await _setup_sweep(db_session)
    svc = PairwiseCalibrationService(db_session)
    sub = _submission(
        pair_id=pair.id, sweep_id=sweep.id, reviewer_id="r1"
    )
    a = uuid4()
    b = uuid4()
    first = await svc.submit_annotation(
        sub,
        **_COMMON_ANN_KWARGS,
        display_a_trial_id=pair.baseline_trial_id,
        display_b_trial_id=pair.candidate_trial_id,
    )
    second = await svc.submit_annotation(
        sub,
        **_COMMON_ANN_KWARGS,
        display_a_trial_id=pair.baseline_trial_id,
        display_b_trial_id=pair.candidate_trial_id,
    )
    assert first.status == "created"
    assert second.status == "existing"
    assert first.annotation.id == second.annotation.id
    # silence unused locals for ruff
    _ = (a, b)


@pytest.mark.asyncio
async def test_annotation_payload_conflict_returns_409(
    db_session: AsyncSession,
) -> None:
    sweep, pair = await _setup_sweep(db_session)
    svc = PairwiseCalibrationService(db_session)
    sub1 = _submission(
        pair_id=pair.id, sweep_id=sweep.id, reviewer_id="r1", raw_winner="a"
    )
    await svc.submit_annotation(
        sub1,
        **_COMMON_ANN_KWARGS,
        display_a_trial_id=pair.baseline_trial_id,
        display_b_trial_id=pair.candidate_trial_id,
    )
    # Same reviewer, same surface — but different verdict
    sub2 = _submission(
        pair_id=pair.id, sweep_id=sweep.id, reviewer_id="r1", raw_winner="b",
        norm_winner="candidate",
    )
    with pytest.raises(PairwiseCalibrationError) as ei:
        await svc.submit_annotation(
            sub2,
            **_COMMON_ANN_KWARGS,
            display_a_trial_id=pair.baseline_trial_id,
            display_b_trial_id=pair.candidate_trial_id,
        )
    err = ei.value
    assert err.status_code == 409
    assert err.code == "EVAL_ANNOTATION_PAYLOAD_CONFLICT"


@pytest.mark.asyncio
async def test_third_primary_reviewer_rejected(
    db_session: AsyncSession,
) -> None:
    sweep, pair = await _setup_sweep(db_session)
    svc = PairwiseCalibrationService(db_session)
    for rid in ("r1", "r2"):
        await svc.submit_annotation(
            _submission(pair_id=pair.id, sweep_id=sweep.id, reviewer_id=rid),
            **_COMMON_ANN_KWARGS,
            display_a_trial_id=pair.baseline_trial_id,
            display_b_trial_id=pair.candidate_trial_id,
        )
    with pytest.raises(PairwiseCalibrationError) as ei:
        await svc.submit_annotation(
            _submission(pair_id=pair.id, sweep_id=sweep.id, reviewer_id="r3"),
            **_COMMON_ANN_KWARGS,
            display_a_trial_id=pair.baseline_trial_id,
            display_b_trial_id=pair.candidate_trial_id,
        )
    assert ei.value.code == "EVAL_ANNOTATION_PRIMARY_REVIEWER_FULL"
    assert ei.value.status_code == 409


@pytest.mark.asyncio
async def test_same_reviewer_re_submit_after_second_other_is_idempotent(
    db_session: AsyncSession,
) -> None:
    """Reviewer r1 submits, r2 submits, then r1 submits AGAIN (identical
    payload) → 200 existing, NOT primary_full."""

    sweep, pair = await _setup_sweep(db_session)
    svc = PairwiseCalibrationService(db_session)
    sub_r1 = _submission(
        pair_id=pair.id, sweep_id=sweep.id, reviewer_id="r1"
    )
    sub_r2 = _submission(
        pair_id=pair.id, sweep_id=sweep.id, reviewer_id="r2"
    )
    await svc.submit_annotation(
        sub_r1,
        **_COMMON_ANN_KWARGS,
        display_a_trial_id=pair.baseline_trial_id,
        display_b_trial_id=pair.candidate_trial_id,
    )
    await svc.submit_annotation(
        sub_r2,
        **_COMMON_ANN_KWARGS,
        display_a_trial_id=pair.baseline_trial_id,
        display_b_trial_id=pair.candidate_trial_id,
    )
    replay = await svc.submit_annotation(
        sub_r1,
        **_COMMON_ANN_KWARGS,
        display_a_trial_id=pair.baseline_trial_id,
        display_b_trial_id=pair.candidate_trial_id,
    )
    assert replay.status == "existing"


# ===========================================================================
# Adjudication submit
# ===========================================================================


async def _seed_two_primaries(
    db_session: AsyncSession,
    *,
    p1_winner: str = "baseline",
    p2_winner: str = "candidate",
    p1_dim: str = "baseline",
    p2_dim: str = "candidate",
) -> tuple[EvalPairwiseSweep, EvalTrialPair]:
    sweep, pair = await _setup_sweep(db_session)
    svc = PairwiseCalibrationService(db_session)
    await svc.submit_annotation(
        _submission(
            pair_id=pair.id,
            sweep_id=sweep.id,
            reviewer_id="r1",
            norm_winner=p1_winner,
            norm_dim_val=p1_dim,
        ),
        **_COMMON_ANN_KWARGS,
        display_a_trial_id=pair.baseline_trial_id,
        display_b_trial_id=pair.candidate_trial_id,
    )
    await svc.submit_annotation(
        _submission(
            pair_id=pair.id,
            sweep_id=sweep.id,
            reviewer_id="r2",
            norm_winner=p2_winner,
            norm_dim_val=p2_dim,
        ),
        **_COMMON_ANN_KWARGS,
        display_a_trial_id=pair.baseline_trial_id,
        display_b_trial_id=pair.candidate_trial_id,
    )
    return sweep, pair


@pytest.mark.asyncio
async def test_adjudication_requires_disagreement(db_session: AsyncSession) -> None:
    """Two primaries agreeing on EVERYTHING → no adjudication allowed."""

    sweep, pair = await _seed_two_primaries(
        db_session, p1_winner="baseline", p2_winner="baseline",
        p1_dim="baseline", p2_dim="baseline",
    )
    svc = PairwiseCalibrationService(db_session)
    with pytest.raises(PairwiseCalibrationError) as ei:
        await svc.submit_adjudication(
            _submission(
                pair_id=pair.id,
                sweep_id=sweep.id,
                reviewer_id="r3",
                norm_winner="baseline",
                is_adjudication=True,
            ),
            **_COMMON_ANN_KWARGS,
            display_a_trial_id=pair.baseline_trial_id,
            display_b_trial_id=pair.candidate_trial_id,
        )
    assert ei.value.code == "EVAL_ADJUDICATION_PRECONDITION_FAILED"


@pytest.mark.asyncio
async def test_dim_only_disagreement_allows_adjudication(
    db_session: AsyncSession,
) -> None:
    """Overall winner agrees but ONE dimension differs → disagreement
    exists, adjudication is permitted."""

    sweep, pair = await _seed_two_primaries(
        db_session,
        p1_winner="baseline", p2_winner="baseline",  # same overall
        p1_dim="baseline",
        p2_dim="candidate",  # but dimension differs
    )
    svc = PairwiseCalibrationService(db_session)
    result = await svc.submit_adjudication(
        _submission(
            pair_id=pair.id,
            sweep_id=sweep.id,
            reviewer_id="r3",
            norm_winner="baseline",
            is_adjudication=True,
        ),
        **_COMMON_ANN_KWARGS,
        display_a_trial_id=pair.baseline_trial_id,
        display_b_trial_id=pair.candidate_trial_id,
    )
    assert result.status == "created"
    assert result.annotation.reviewer_role == "adjudicator"


@pytest.mark.asyncio
async def test_adjudicator_must_be_third_reviewer(
    db_session: AsyncSession,
) -> None:
    sweep, pair = await _seed_two_primaries(
        db_session, p1_winner="baseline", p2_winner="candidate"
    )
    svc = PairwiseCalibrationService(db_session)
    with pytest.raises(PairwiseCalibrationError) as ei:
        await svc.submit_adjudication(
            _submission(
                pair_id=pair.id,
                sweep_id=sweep.id,
                reviewer_id="r1",  # primary reviewer
                norm_winner="baseline",
                is_adjudication=True,
            ),
            **_COMMON_ANN_KWARGS,
            display_a_trial_id=pair.baseline_trial_id,
            display_b_trial_id=pair.candidate_trial_id,
        )
    assert ei.value.code == "EVAL_ADJUDICATION_PRECONDITION_FAILED"


@pytest.mark.asyncio
async def test_adjudication_unique_second_rejected(
    db_session: AsyncSession,
) -> None:
    sweep, pair = await _seed_two_primaries(
        db_session, p1_winner="baseline", p2_winner="candidate"
    )
    svc = PairwiseCalibrationService(db_session)
    first = await svc.submit_adjudication(
        _submission(
            pair_id=pair.id,
            sweep_id=sweep.id,
            reviewer_id="r3",
            norm_winner="baseline",
            is_adjudication=True,
        ),
        **_COMMON_ANN_KWARGS,
        display_a_trial_id=pair.baseline_trial_id,
        display_b_trial_id=pair.candidate_trial_id,
    )
    assert first.status == "created"
    with pytest.raises(PairwiseCalibrationError) as ei:
        await svc.submit_adjudication(
            _submission(
                pair_id=pair.id,
                sweep_id=sweep.id,
                reviewer_id="r4",  # different reviewer but Pair already has adj
                norm_winner="baseline",
                is_adjudication=True,
            ),
            **_COMMON_ANN_KWARGS,
            display_a_trial_id=pair.baseline_trial_id,
            display_b_trial_id=pair.candidate_trial_id,
        )
    assert ei.value.code == "EVAL_ADJUDICATION_PRECONDITION_FAILED"


# ===========================================================================
# Cancel request
# ===========================================================================


@pytest.mark.asyncio
async def test_cancel_request_only_stages_cancel_not_terminal(
    db_session: AsyncSession,
) -> None:
    sweep, _pair = await _setup_sweep(db_session)
    svc = PairwiseCalibrationService(db_session)
    staged = await svc.request_sweep_cancel(sweep.id)
    assert staged is True
    async with session_transaction(db_session):
        refetched = await EvalRepository(db_session).get_sweep(sweep.id)
    assert refetched is not None
    assert refetched.cancel_requested_at is not None
    assert refetched.status == "running"  # NOT cancelled
    assert refetched.terminal_at is None


@pytest.mark.asyncio
async def test_cancel_request_idempotent_on_already_staged(
    db_session: AsyncSession,
) -> None:
    sweep, _pair = await _setup_sweep(db_session)
    svc = PairwiseCalibrationService(db_session)
    first = await svc.request_sweep_cancel(sweep.id)
    second = await svc.request_sweep_cancel(sweep.id)
    assert first is True
    assert second is False  # already staged


@pytest.mark.asyncio
async def test_cancel_request_unknown_sweep_raises_404(
    db_session: AsyncSession,
) -> None:
    svc = PairwiseCalibrationService(db_session)
    with pytest.raises(PairwiseCalibrationError) as ei:
        await svc.request_sweep_cancel(uuid4())
    assert ei.value.status_code == 404
    assert ei.value.code == "EVAL_SWEEP_NOT_FOUND"


# ===========================================================================
# Calibration report create-or-reuse
# ===========================================================================


@pytest.mark.asyncio
async def test_calibration_report_idempotent_when_same_input_and_content(
    db_session: AsyncSession,
) -> None:
    svc = PairwiseCalibrationService(db_session)
    payload: dict[str, object] = {
        "metrics": {"kappa": 0.0},
        "status": "insufficient",
    }
    sweep_ids = [uuid4()]
    first_status, first = await svc.create_or_reuse_calibration_report(
        report_payload=payload,
        dataset_id="ds",
        dataset_version="v1",
        source_sha256=_VALID_SHA,
        judge_model_id="m1",
        judge_prompt_version="v1",
        judge_rubric_version="v1",
        annotation_schema_version="v1",
        calibration_policy_version="v1",
        sweep_ids=sweep_ids,
        judge_result_snapshot=[],
        annotation_snapshot=[],
        requested_by="r1",
    )
    second_status, second = await svc.create_or_reuse_calibration_report(
        report_payload=payload,
        dataset_id="ds",
        dataset_version="v1",
        source_sha256=_VALID_SHA,
        judge_model_id="m1",
        judge_prompt_version="v1",
        judge_rubric_version="v1",
        annotation_schema_version="v1",
        calibration_policy_version="v1",
        sweep_ids=sweep_ids,
        judge_result_snapshot=[],
        annotation_snapshot=[],
        requested_by="r1",
    )
    assert first_status == "created"
    assert second_status == "existing"
    assert first.id == second.id


@pytest.mark.asyncio
async def test_calibration_report_integrity_violation_on_content_mismatch(
    db_session: AsyncSession,
) -> None:
    svc = PairwiseCalibrationService(db_session)
    fixed_sweep_ids = [uuid4()]

    async def call(report_payload: dict[str, object]) -> None:
        await svc.create_or_reuse_calibration_report(
            report_payload=report_payload,
            dataset_id="ds",
            dataset_version="v1",
            source_sha256=_VALID_SHA,
            judge_model_id="m1",
            judge_prompt_version="v1",
            judge_rubric_version="v1",
            annotation_schema_version="v1",
            calibration_policy_version="v1",
            sweep_ids=fixed_sweep_ids,
            judge_result_snapshot=[],
            annotation_snapshot=[],
            requested_by="r1",
        )

    await call({"status": "insufficient"})
    with pytest.raises(PairwiseCalibrationError) as ei:
        await call({"status": "failing"})  # same input, different content
    err = ei.value
    assert err.status_code == 500
    assert err.code == "EVAL_CALIBRATION_INTEGRITY_VIOLATION"


# ===========================================================================
# materialize_sweep_items happy path
# ===========================================================================


@pytest.mark.asyncio
async def test_materialize_creates_items_and_flips_sweep_to_running(
    db_session: AsyncSession,
) -> None:
    sweep = await _repo_make_sweep(
        db_session, requested_pair_count=2, status="queued"
    )
    async with session_transaction(db_session):
        await EvalRepository(db_session).create_sweep(sweep)
    pair1 = await _seed_pair(db_session, 1)
    pair2 = await _seed_pair(db_session, 2)

    seeds: list[SweepItemSeed] = []
    for pair in (pair1, pair2):
        for pos in (PositionVariant.BASELINE, PositionVariant.SWAPPED):
            seeds.append(
                SweepItemSeed(
                    pair_id=pair.id,
                    pair_hash=pair.pair_hash,
                    case_id=pair.case_id,
                    baseline_trial_id=pair.baseline_trial_id,
                    candidate_trial_id=pair.candidate_trial_id,
                    baseline_output_hash=_VALID_SHA,
                    candidate_output_hash=_VALID_SHA,
                    frozen_review_surface_sha256=_VALID_SHA,
                    display_a_trial_id=(
                        pair.baseline_trial_id
                        if pos is PositionVariant.BASELINE
                        else pair.candidate_trial_id
                    ),
                    display_b_trial_id=(
                        pair.candidate_trial_id
                        if pos is PositionVariant.BASELINE
                        else pair.baseline_trial_id
                    ),
                    position_variant=pos,
                )
            )

    svc = PairwiseCalibrationService(db_session)
    items = await svc.materialize_sweep_items(
        sweep=sweep, seeds=seeds, annotation_schema_version="v1"
    )
    assert len(items) == 4
    # Same (sweep, pair, position) → deterministic judge_run_id on retry
    from app.services.pairwise_calibration import deterministic_judge_run_id
    expected_first = deterministic_judge_run_id(
        sweep_id=sweep.id,
        pair_hash=seeds[0].pair_hash,
        position_variant=seeds[0].position_variant,
        judge_model_id=sweep.judge_model_id,
        judge_prompt_version=sweep.judge_prompt_version,
        judge_rubric_version=sweep.judge_rubric_version,
    )
    assert items[0].judge_run_id == expected_first
    async with session_transaction(db_session):
        refetched = await EvalRepository(db_session).get_sweep(sweep.id)
    assert refetched is not None
    assert refetched.status == "running"


@pytest.mark.asyncio
async def test_materialize_rejects_duplicate_pair_position_seed(
    db_session: AsyncSession,
) -> None:
    sweep = await _repo_make_sweep(db_session, requested_pair_count=1, status="queued")
    async with session_transaction(db_session):
        await EvalRepository(db_session).create_sweep(sweep)
    pair = await _seed_pair(db_session, 1)
    seeds = [
        SweepItemSeed(
            pair_id=pair.id, pair_hash=pair.pair_hash, case_id=pair.case_id,
            baseline_trial_id=pair.baseline_trial_id,
            candidate_trial_id=pair.candidate_trial_id,
            baseline_output_hash=_VALID_SHA, candidate_output_hash=_VALID_SHA,
            frozen_review_surface_sha256=_VALID_SHA,
            display_a_trial_id=pair.baseline_trial_id,
            display_b_trial_id=pair.candidate_trial_id,
            position_variant=PositionVariant.BASELINE,
        ),
        SweepItemSeed(  # duplicate (pair, position)
            pair_id=pair.id, pair_hash=pair.pair_hash, case_id=pair.case_id,
            baseline_trial_id=pair.baseline_trial_id,
            candidate_trial_id=pair.candidate_trial_id,
            baseline_output_hash=_VALID_SHA, candidate_output_hash=_VALID_SHA,
            frozen_review_surface_sha256=_VALID_SHA,
            display_a_trial_id=pair.baseline_trial_id,
            display_b_trial_id=pair.candidate_trial_id,
            position_variant=PositionVariant.BASELINE,
        ),
    ]
    svc = PairwiseCalibrationService(db_session)
    with pytest.raises(PairwiseCalibrationError) as ei:
        await svc.materialize_sweep_items(
            sweep=sweep, seeds=seeds, annotation_schema_version="v1"
        )
    assert ei.value.status_code == 422
    assert ei.value.code == "EVAL_SWEEP_SEEDS_DUPLICATED"


# silence ruff unused import guard
_ = (_config, EvalPairwiseHumanAnnotation, canonical_sha256, JUDGE_ALLOWED_KINDS)
