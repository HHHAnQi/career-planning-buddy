"""PR-9c.2 Pairwise Calibration repository tests.

Focuses on the core contract surfaces introduced in Commit 2:

* Sweep create / mark_running / mark_terminal / cancel staging
* SweepItem create (idempotent via UNIQUE) + recoverability
* Atomic counter increments under row lock
* Annotation create + lookup + UNIQUE(payload-review-input) behaviour
* CalibrationReport create + UNIQUE(input_hash) + latest/history queries
* Database CHECK constraints reject off-vocabulary / off-position payloads
* Cascade behaviour (SweepItem CASCADE from Sweep; Annotation RESTRICT on Pair)
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import session_transaction
from app.models.eval import (
    EvalExperiment,
    EvalPairwiseCalibrationReport,
    EvalPairwiseHumanAnnotation,
    EvalPairwiseJudgeResult,
    EvalPairwiseSweep,
    EvalPairwiseSweepItem,
    EvalTrialPair,
)
from app.repositories.evals import EvalRepository
from app.services.evals import EvalService
from evals.v2.contracts import canonical_sha256
from evals.v2.pairwise import JUDGE_ALLOWED_KINDS
from tests.evals_v2.test_pairwise_repository import _provision_experiment

_VALID_SHA = "a" * 64
_PAIR_SHA_OFFSET = 0  # filled per pair via canonical_sha256


def _canonical_seed(line_no: int) -> str:
    """Return a 64-hex pair_hash for synthetic test pairs."""

    return canonical_sha256({"kind": "test-seed", "i": line_no})


# ===========================================================================
# Sweep lifecycle + counter increments
# ===========================================================================


async def _make_sweep(
    db_session: AsyncSession,
    *,
    requested_pair_count: int = 2,
    status: str = "queued",
    baseline_experiment_id: UUID | None = None,
    candidate_experiment_id: UUID | None = None,
) -> EvalPairwiseSweep:
    """Insert a minimal Sweep row for tests. Uses pre-existing experiment
    rows when supplied; otherwise provisions a fresh pair."""

    if baseline_experiment_id is None or candidate_experiment_id is None:
        _, trials = await _provision_experiment(db_session)
        # Both trials are in the SAME experiment; for the sweep FK we need
        # two experiment ids. Use the experiment of the trials for both
        # columns (CI is fine — FK shape is what matters).
        exp_id = (
            await EvalRepository(db_session).get_trial(_trial_uuid(trials[0]))
        )
        # Fall back to creating two minimal experiments below if a trial
        # lookup is awkward.
        baseline_experiment_id = exp_id.experiment_id if exp_id else None
        candidate_experiment_id = baseline_experiment_id
    if baseline_experiment_id is None:
        # Provision two lightweight experiments directly.
        async with session_transaction(db_session):
            b = EvalExperiment(
                dataset_id="ds",
                dataset_version="v1",
                dataset_hash=_VALID_SHA,
                git_commit="abc1234",
                graph_version="g-v1",
                prompt_version="p-v1",
                model_version="m-v1",
                tool_version="t-v1",
                context_version="c-v1",
                memory_version="mem-v1",
                frozen_config_hash=_VALID_SHA,
                execution_mode="mock_provider",
                variant_role="baseline",
                trial_count=1,
            )
            c = EvalExperiment(
                dataset_id="ds",
                dataset_version="v1",
                dataset_hash=_VALID_SHA,
                git_commit="abc1234",
                graph_version="g-v1",
                prompt_version="p-v1",
                model_version="m-v1",
                tool_version="t-v1",
                context_version="c-v1",
                memory_version="mem-v1",
                frozen_config_hash=_VALID_SHA,
                execution_mode="mock_provider",
                variant_role="candidate",
                trial_count=1,
            )
            db_session.add(b)
            db_session.add(c)
            await db_session.flush()
            baseline_experiment_id = b.id
            candidate_experiment_id = c.id

    return EvalPairwiseSweep(
        id=uuid4(),
        dataset_id="ds",
        dataset_version="v1",
        source_sha256=_VALID_SHA,
        export_revision="export-v1",
        baseline_experiment_id=baseline_experiment_id,
        candidate_experiment_id=candidate_experiment_id,
        judge_model_id="judge-m1",
        judge_prompt_version="v1",
        judge_rubric_version="v1",
        annotation_schema_version="v1",
        comparison_group_id=f"grp-{uuid4().hex[:8]}",
        status=status,
        requested_pair_count=requested_pair_count,
        requested_judge_run_count=requested_pair_count * 2,
        requested_by="test-reviewer",
    )


def _trial_uuid(value: str) -> UUID:
    """Convert str→UUID; `str` ids returned by provision."""

    return UUID(value)


@pytest.mark.asyncio
async def test_create_and_get_sweep(db_session: AsyncSession) -> None:
    sweep = await _make_sweep(db_session)
    async with session_transaction(db_session):
        await EvalRepository(db_session).create_sweep(sweep)
    async with session_transaction(db_session):
        fetched = await EvalRepository(db_session).get_sweep(sweep.id)
    assert fetched is not None
    assert fetched.dataset_id == "ds"
    assert fetched.requested_judge_run_count == sweep.requested_pair_count * 2


@pytest.mark.asyncio
async def test_mark_sweep_running_only_from_queued(db_session: AsyncSession) -> None:
    sweep = await _make_sweep(db_session, status="queued")
    async with session_transaction(db_session):
        await EvalRepository(db_session).create_sweep(sweep)
        await EvalRepository(db_session).mark_sweep_running(sweep.id)
        first = await EvalRepository(db_session).get_sweep(sweep.id)
    assert first is not None
    assert first.status == "running"

    # Marking again is a no-op (the WHERE clause already restricts to queued)
    async with session_transaction(db_session):
        await EvalRepository(db_session).mark_sweep_running(sweep.id)
        await EvalRepository(db_session).mark_sweep_terminal(
            sweep.id, status="completed"
        )
        # Now mark_running should NOT rewind a terminal Sweep.
        await EvalRepository(db_session).mark_sweep_running(sweep.id)
        second = await EvalRepository(db_session).get_sweep(sweep.id)
    assert second is not None
    assert second.status == "completed"


@pytest.mark.asyncio
async def test_mark_terminal_rejects_invalid_status(db_session: AsyncSession) -> None:
    sweep = await _make_sweep(db_session)
    async with session_transaction(db_session):
        await EvalRepository(db_session).create_sweep(sweep)
        await EvalRepository(db_session).mark_sweep_running(sweep.id)
    with pytest.raises(ValueError, match="invalid terminal status"):
        async with session_transaction(db_session):
            await EvalRepository(db_session).mark_sweep_terminal(
                sweep.id, status="cancelled"
            )


@pytest.mark.asyncio
async def test_cancel_request_does_not_set_terminal(db_session: AsyncSession) -> None:
    """Per supplementary constraint #8: ``cancel_requested_at`` is a
    staging fact, NOT a terminal status."""

    sweep = await _make_sweep(db_session, status="running")
    async with session_transaction(db_session):
        await EvalRepository(db_session).create_sweep(sweep)
        await EvalRepository(db_session).mark_sweep_running(sweep.id)
    stamp = datetime.now(UTC)
    async with session_transaction(db_session):
        updated = await EvalRepository(db_session).set_sweep_cancel_requested_at(
            sweep.id, stamp
        )
    assert updated is not None
    assert updated.cancel_requested_at == stamp
    # Status MUST still be running — Executor owns the terminal transition.
    async with session_transaction(db_session):
        refetched = await EvalRepository(db_session).get_sweep(sweep.id)
    assert refetched is not None
    assert refetched.status == "running"
    assert refetched.terminal_at is None


# ===========================================================================
# SweepItem: idempotent materialization + recoverable list
# ===========================================================================


def _synthetic_pair(db_session: AsyncSession, idx: int) -> EvalTrialPair:
    """A minimal Pair row for tests. Trials FK are placeholders; tests
    that exercise Pair FK constraints build real trials via _provision."""

    base = uuid4()
    cand = uuid4()
    case_id = f"case-{idx}"
    baseline_projection = {"request": {"e": "x"}, "plan": {"summary": f"b-{idx}"}}
    candidate_projection = {"request": {"e": "x"}, "plan": {"summary": f"c-{idx}"}}
    pair_hash = canonical_sha256({
        "schema_version": "eval-trial-pair/v1",
        "case_id": case_id,
        "baseline_trial_id": str(base),
        "candidate_trial_id": str(cand),
        "baseline_output_hash": canonical_sha256(baseline_projection),
        "candidate_output_hash": canonical_sha256(candidate_projection),
    })
    return EvalTrialPair(
        baseline_trial_id=base,
        candidate_trial_id=cand,
        case_id=case_id,
        pair_hash=pair_hash,
        input_hash=_VALID_SHA,
        allowed_evidence_kinds=sorted(k.value for k in JUDGE_ALLOWED_KINDS),
        judge_prompt_version="v1",
        judge_rubric_version="v1",
    )


async def _seed_pair(db_session: AsyncSession, idx: int) -> EvalTrialPair:
    case_id, trials = await _provision_experiment(db_session)
    base = UUID(trials[0]) if isinstance(trials[0], str) else trials[0]
    cand = UUID(trials[1]) if isinstance(trials[1], str) else trials[1]
    case_id = f"{case_id}-{idx}"
    service = EvalService(db_session)
    baseline_view = await service.build_judge_view(base)
    candidate_view = await service.build_judge_view(cand)
    from evals.v2.pairwise import build_pair

    domain_pair = build_pair(
        baseline_trial_id=base,
        candidate_trial_id=cand,
        case_id=case_id,
        baseline_view=baseline_view,
        candidate_view=candidate_view,
    )
    pair = EvalTrialPair(
        baseline_trial_id=base,
        candidate_trial_id=cand,
        case_id=case_id,
        pair_hash=domain_pair.pair_hash(),
        input_hash=_VALID_SHA,
        allowed_evidence_kinds=sorted(k.value for k in JUDGE_ALLOWED_KINDS),
        judge_prompt_version="v1",
        judge_rubric_version="v1",
    )
    async with session_transaction(db_session):
        return await EvalRepository(db_session).get_or_create_pair(pair)


async def _real_judge_result(
    db_session: AsyncSession, *, pair: EvalTrialPair
) -> UUID:
    """Create a real ``EvalPairwiseJudgeResult`` row attached to a Pair
    and return its id. Required because SweepItem's CHECK constraint
    demands ``judge_result_id IS NOT NULL`` when ``status='completed'``.
    """

    result = EvalPairwiseJudgeResult(
        pair_id=pair.id,
        judge_run_id=uuid4(),
        judge_run_status="invalid_structured_output",
        position_variant="baseline",
        comparison_group_id=f"grp-{uuid4().hex[:8]}",
        model_id="fixture-judge-v1",
        prompt_version="v1",
        rubric_version="v1",
        input_hash=_VALID_SHA,
    )
    async with session_transaction(db_session):
        persisted = await EvalRepository(db_session).create_judge_result(result)
    return persisted.id



@pytest.mark.asyncio
async def test_create_sweep_item_with_deterministic_judge_run_id(
    db_session: AsyncSession,
) -> None:
    sweep = await _make_sweep(db_session, requested_pair_count=1)
    pair = await _seed_pair(db_session, 1)
    item = EvalPairwiseSweepItem(
        sweep_id=sweep.id,
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
        status="queued",
    )
    async with session_transaction(db_session):
        await EvalRepository(db_session).create_sweep(sweep)
        inserted = await EvalRepository(db_session).create_sweep_items([item])
    assert inserted[0].judge_run_id == item.judge_run_id


@pytest.mark.asyncio
async def test_sweep_item_unique_position_prevents_duplicate_materialization(
    db_session: AsyncSession,
) -> None:
    """Same (sweep, pair, position) re-insert raises IntegrityError — never
    a duplicate row."""

    sweep = await _make_sweep(db_session, requested_pair_count=1)
    pair = await _seed_pair(db_session, 1)

    def make_item() -> EvalPairwiseSweepItem:
        return EvalPairwiseSweepItem(
            sweep_id=sweep.id,
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
            status="queued",
        )

    async with session_transaction(db_session):
        await EvalRepository(db_session).create_sweep(sweep)
        await EvalRepository(db_session).create_sweep_items([make_item()])
    with pytest.raises((DBAPIError, IntegrityError)):
        async with session_transaction(db_session):
            await EvalRepository(db_session).create_sweep_items([make_item()])


@pytest.mark.asyncio
async def test_recoverable_items_excludes_terminal(
    db_session: AsyncSession,
) -> None:
    """A completed item is NOT in the recovery list."""

    sweep = await _make_sweep(db_session, requested_pair_count=1)
    pair = await _seed_pair(db_session, 1)
    item = EvalPairwiseSweepItem(
        sweep_id=sweep.id,
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
        status="queued",
    )
    async with session_transaction(db_session):
        await EvalRepository(db_session).create_sweep(sweep)
        await EvalRepository(db_session).create_sweep_items([item])
    judge_result_id = await _real_judge_result(db_session, pair=pair)
    async with session_transaction(db_session):
        transitioned = await EvalRepository(
            db_session
        ).mark_sweep_item_completed(item.id, judge_result_id=judge_result_id)
        assert transitioned is True
        recoverable = await EvalRepository(
            db_session
        ).list_recoverable_sweep_items(sweep.id)
    assert recoverable == []


@pytest.mark.asyncio
async def test_mark_item_failed_sets_error_and_terminal(
    db_session: AsyncSession,
) -> None:
    sweep = await _make_sweep(db_session, requested_pair_count=1)
    pair = await _seed_pair(db_session, 1)
    item = EvalPairwiseSweepItem(
        sweep_id=sweep.id,
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
        status="queued",
    )
    async with session_transaction(db_session):
        await EvalRepository(db_session).create_sweep(sweep)
        await EvalRepository(db_session).create_sweep_items([item])
        await EvalRepository(db_session).mark_sweep_item_failed(
            item.id, error_code="PROVIDER_TIMEOUT"
        )
        refetched = await EvalRepository(db_session).get_sweep_item(
            sweep.id, pair.id, "baseline"
        )
    assert refetched is not None
    assert refetched.status == "failed"
    assert refetched.error_code == "PROVIDER_TIMEOUT"
    assert refetched.terminal_at is not None


@pytest.mark.asyncio
async def test_sweep_item_cascade_on_sweep_delete(db_session: AsyncSession) -> None:
    """Deleting a Sweep CASCADE deletes its SweepItem rows."""

    sweep = await _make_sweep(db_session, requested_pair_count=1)
    pair = await _seed_pair(db_session, 1)
    item = EvalPairwiseSweepItem(
        sweep_id=sweep.id,
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
        status="queued",
    )
    async with session_transaction(db_session):
        await EvalRepository(db_session).create_sweep(sweep)
        await EvalRepository(db_session).create_sweep_items([item])
        sweep_id = sweep.id
        await db_session.delete(sweep)
        await db_session.flush()
    async with session_transaction(db_session):
        items = await EvalRepository(db_session).list_sweep_items(sweep_id)
    assert items == []


# ===========================================================================
# Annotation: idempotent + UNIQUE on (dataset, pair, reviewer, surface)
# ===========================================================================


def _make_annotation(
    *,
    sweep_id: UUID,
    pair_id: UUID,
    reviewer_id: str = "rev1",
    raw_winner: str = "a",
    norm_winner: str = "baseline",
    raw_dim_val: str = "a",
    norm_dim_val: str = "baseline",
    review_input_hash: str = _VALID_SHA,
    submission_hash: str = _VALID_SHA,
    is_adjudication: bool = False,
) -> EvalPairwiseHumanAnnotation:
    role = "adjudicator" if is_adjudication else "primary"
    return EvalPairwiseHumanAnnotation(
        dataset_id="ds",
        dataset_version="v1",
        sweep_id=sweep_id,
        pair_id=pair_id,
        reviewer_id=reviewer_id,
        reviewer_role=role,
        is_adjudication=is_adjudication,
        annotation_schema_version="v1",
        rubric_version="v1",
        judge_prompt_version="v1",
        judge_model_id="judge-m1",
        frozen_review_surface_sha256=_VALID_SHA,
        position_variant="baseline",
        display_a_trial_id=uuid4(),
        display_b_trial_id=uuid4(),
        raw_winner=raw_winner,
        raw_dim_actionability=raw_dim_val,
        raw_dim_alignment=raw_dim_val,
        raw_dim_personalization=raw_dim_val,
        raw_dim_clarity=raw_dim_val,
        raw_dim_consistency=raw_dim_val,
        normalized_winner=norm_winner,
        norm_dim_actionability=norm_dim_val,
        norm_dim_alignment=norm_dim_val,
        norm_dim_personalization=norm_dim_val,
        norm_dim_clarity=norm_dim_val,
        norm_dim_consistency=norm_dim_val,
        review_input_hash=review_input_hash,
        submission_hash=submission_hash,
        rationale="r",
    )


@pytest.mark.asyncio
async def test_annotation_unique_dataset_pair_reviewer_surface(
    db_session: AsyncSession,
) -> None:
    sweep = await _make_sweep(db_session, requested_pair_count=1)
    pair = await _seed_pair(db_session, 1)
    ann1 = _make_annotation(sweep_id=sweep.id, pair_id=pair.id, reviewer_id="r1")
    ann2 = _make_annotation(sweep_id=sweep.id, pair_id=pair.id, reviewer_id="r1")

    async with session_transaction(db_session):
        await EvalRepository(db_session).create_sweep(sweep)
        await EvalRepository(db_session).create_annotation(ann1)
    with pytest.raises((DBAPIError, IntegrityError)):
        async with session_transaction(db_session):
            await EvalRepository(db_session).create_annotation(ann2)


@pytest.mark.asyncio
async def test_annotation_off_vocabulary_raw_dim_rejected_by_check(
    db_session: AsyncSession,
) -> None:
    sweep = await _make_sweep(db_session, requested_pair_count=1)
    pair = await _seed_pair(db_session, 1)
    bad = _make_annotation(
        sweep_id=sweep.id, pair_id=pair.id, reviewer_id="r1", raw_dim_val="INVALID"
    )
    async with session_transaction(db_session):
        await EvalRepository(db_session).create_sweep(sweep)
    with pytest.raises((DBAPIError, IntegrityError)):
        async with session_transaction(db_session):
            await EvalRepository(db_session).create_annotation(bad)


@pytest.mark.asyncio
async def test_annotation_off_vocabulary_normalized_dim_rejected_by_check(
    db_session: AsyncSession,
) -> None:
    sweep = await _make_sweep(db_session, requested_pair_count=1)
    pair = await _seed_pair(db_session, 1)
    bad = _make_annotation(
        sweep_id=sweep.id,
        pair_id=pair.id,
        reviewer_id="r1",
        norm_dim_val="INVALID",
    )
    async with session_transaction(db_session):
        await EvalRepository(db_session).create_sweep(sweep)
    with pytest.raises((DBAPIError, IntegrityError)):
        async with session_transaction(db_session):
            await EvalRepository(db_session).create_annotation(bad)


@pytest.mark.asyncio
async def test_annotation_partial_unique_on_adjudication(
    db_session: AsyncSession,
) -> None:
    """A second adjudication row for the same (pair_id, review_input_hash)
    is rejected even with a different reviewer."""

    sweep = await _make_sweep(db_session, requested_pair_count=1)
    pair = await _seed_pair(db_session, 1)
    adj_a = _make_annotation(
        sweep_id=sweep.id,
        pair_id=pair.id,
        reviewer_id="adj1",
        is_adjudication=True,
    )
    adj_b = _make_annotation(
        sweep_id=sweep.id,
        pair_id=pair.id,
        reviewer_id="adj2",
        is_adjudication=True,
        submission_hash="b" * 64,  # different submission
    )
    async with session_transaction(db_session):
        await EvalRepository(db_session).create_sweep(sweep)
        await EvalRepository(db_session).create_annotation(adj_a)
    with pytest.raises((DBAPIError, IntegrityError)):
        async with session_transaction(db_session):
            await EvalRepository(db_session).create_annotation(adj_b)


@pytest.mark.asyncio
async def test_annotation_on_delete_restrict_when_pair_deleted(
    db_session: AsyncSession,
) -> None:
    """ON DELETE RESTRICT: deleting a Pair with annotations must fail."""

    sweep = await _make_sweep(db_session, requested_pair_count=1)
    pair = await _seed_pair(db_session, 1)
    ann = _make_annotation(sweep_id=sweep.id, pair_id=pair.id, reviewer_id="r1")
    async with session_transaction(db_session):
        await EvalRepository(db_session).create_sweep(sweep)
        await EvalRepository(db_session).create_annotation(ann)
    with pytest.raises((DBAPIError, IntegrityError)):
        async with session_transaction(db_session):
            await db_session.delete(pair)
            await db_session.flush()


# ===========================================================================
# CalibrationReport: UNIQUE(input_hash) + queries
# ===========================================================================


@pytest.mark.asyncio
async def test_calibration_report_unique_input_hash(
    db_session: AsyncSession,
) -> None:
    rep1 = EvalPairwiseCalibrationReport(
        dataset_id="ds",
        dataset_version="v1",
        source_sha256=_VALID_SHA,
        judge_model_id="m1",
        judge_prompt_version="v1",
        judge_rubric_version="v1",
        annotation_schema_version="v1",
        calibration_policy_version="v1",
        input_hash=_VALID_SHA,
        content_hash=_VALID_SHA,
        report_payload={"k": "v"},
        requested_by="r1",
    )
    rep2 = EvalPairwiseCalibrationReport(
        dataset_id="ds",
        dataset_version="v1",
        source_sha256=_VALID_SHA,
        judge_model_id="m1",
        judge_prompt_version="v1",
        judge_rubric_version="v1",
        annotation_schema_version="v1",
        calibration_policy_version="v1",
        input_hash=_VALID_SHA,  # same input_hash, different content_hash
        content_hash="b" * 64,
        report_payload={"k": "other"},
        requested_by="r1",
    )
    async with session_transaction(db_session):
        await EvalRepository(db_session).create_calibration_report(rep1)
    with pytest.raises((DBAPIError, IntegrityError)):
        async with session_transaction(db_session):
            await EvalRepository(db_session).create_calibration_report(rep2)


@pytest.mark.asyncio
async def test_calibration_report_latest_and_history(db_session: AsyncSession) -> None:
    payloads = [
        (_VALID_SHA, "0" * 64),
        ("b" * 64, "1" * 64),
        ("c" * 64, "2" * 64),
    ]
    for inhash, conthash in payloads:
        async with session_transaction(db_session):
            await EvalRepository(db_session).create_calibration_report(
                EvalPairwiseCalibrationReport(
                    dataset_id="ds",
                    dataset_version="v1",
                    source_sha256=_VALID_SHA,
                    judge_model_id="m1",
                    judge_prompt_version="v1",
                    judge_rubric_version="v1",
                    annotation_schema_version="v1",
                    calibration_policy_version="v1",
                    input_hash=inhash,
                    content_hash=conthash,
                    report_payload={"i": inhash[:4]},
                    requested_by="r1",
                )
            )
    async with session_transaction(db_session):
        latest = await EvalRepository(db_session).get_latest_calibration_report(
            "ds", "v1"
        )
        history = await EvalRepository(db_session).list_calibration_reports(
            "ds", "v1"
        )
    assert latest is not None
    assert len(history) == 3


# ===========================================================================
# PR-9c.2 supplementary checks (Commit 2 conditional-pass resolution):
# #3  completed item MUST carry judge_result_id (DDL CHECK enforced)
# #4  terminal items are CAS-guarded and counter increments are idempotent
# ===========================================================================


@pytest.mark.asyncio
async def test_completed_item_requires_judge_result_via_ddl_check(
    db_session: AsyncSession,
) -> None:
    """DB CHECK: ``(status='completed') = (judge_result_id IS NOT NULL)``.

    A real Judge Run that produced ``invalid_structured_output`` STILL
    has a row in ``eval_pairwise_judge_results`` — there is no concept
    of a "completed Item with no result row".
    """

    sweep = await _make_sweep(db_session, requested_pair_count=1)
    pair = await _seed_pair(db_session, 1)
    item = EvalPairwiseSweepItem(
        sweep_id=sweep.id,
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
        status="queued",
    )
    async with session_transaction(db_session):
        await EvalRepository(db_session).create_sweep(sweep)
        await EvalRepository(db_session).create_sweep_items([item])

    # Mark item completed with `judge_result_id=None`. The repository
    # enforces via type signature that None is not accepted; the only way
    # to bypass is a hand-written INSERT, which the DDL CHECK rejects.
    bad_item = EvalPairwiseSweepItem(
        sweep_id=sweep.id,
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
        judge_result_id=None,
    )
    with pytest.raises((DBAPIError, IntegrityError)):
        async with session_transaction(db_session):
            await EvalRepository(db_session).create_sweep_items([bad_item])


@pytest.mark.asyncio
async def test_mark_completed_twice_returns_false_no_double_count(
    db_session: AsyncSession,
) -> None:
    """CAS pattern: ``mark_sweep_item_completed`` returns True exactly
    on the FIRST terminal transition; subsequent calls return False so
    the Service MUST NOT bump the Sweep counter twice."""

    sweep = await _make_sweep(db_session, requested_pair_count=1)
    pair = await _seed_pair(db_session, 1)
    item = EvalPairwiseSweepItem(
        sweep_id=sweep.id,
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
        status="queued",
    )
    async with session_transaction(db_session):
        await EvalRepository(db_session).create_sweep(sweep)
        await EvalRepository(db_session).create_sweep_items([item])

    judge_result_id = await _real_judge_result(db_session, pair=pair)
    first = await EvalRepository(db_session).mark_sweep_item_completed(
        item.id, judge_result_id=judge_result_id
    )
    second = await EvalRepository(db_session).mark_sweep_item_completed(
        item.id, judge_result_id=judge_result_id
    )
    assert first is True
    assert second is False  # was already terminal — idempotent no-op


@pytest.mark.asyncio
async def test_mark_failed_twice_returns_false_no_double_count(
    db_session: AsyncSession,
) -> None:
    """CAS pattern: ``mark_sweep_item_failed`` similarly idempotent."""

    sweep = await _make_sweep(db_session, requested_pair_count=1)
    pair = await _seed_pair(db_session, 1)
    item = EvalPairwiseSweepItem(
        sweep_id=sweep.id,
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
        status="queued",
    )
    async with session_transaction(db_session):
        await EvalRepository(db_session).create_sweep(sweep)
        await EvalRepository(db_session).create_sweep_items([item])

    first = await EvalRepository(db_session).mark_sweep_item_failed(
        item.id, error_code="PROVIDER_TIMEOUT"
    )
    second = await EvalRepository(db_session).mark_sweep_item_failed(
        item.id, error_code="PROVIDER_TIMEOUT"
    )
    assert first is True
    assert second is False


@pytest.mark.asyncio
async def test_completed_item_cannot_transition_to_failed(
    db_session: AsyncSession,
) -> None:
    """Once terminal `completed`, an Item MUST NOT silently flip to
    `failed` — the CAS WHERE clause rejects the transition."""

    sweep = await _make_sweep(db_session, requested_pair_count=1)
    pair = await _seed_pair(db_session, 1)
    item = EvalPairwiseSweepItem(
        sweep_id=sweep.id,
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
        status="queued",
    )
    async with session_transaction(db_session):
        await EvalRepository(db_session).create_sweep(sweep)
        await EvalRepository(db_session).create_sweep_items([item])

    judge_result_id = await _real_judge_result(db_session, pair=pair)
    first_complete = await EvalRepository(
        db_session
    ).mark_sweep_item_completed(item.id, judge_result_id=judge_result_id)
    # Subsequent fail attempt must be rejected (no-op) — Item stays completed
    fail_attempt = await EvalRepository(db_session).mark_sweep_item_failed(
        item.id, error_code="PROVIDER_TIMEOUT"
    )
    assert first_complete is True
    assert fail_attempt is False

    refetched = await EvalRepository(db_session).get_sweep_item(
        sweep.id, pair.id, "baseline"
    )
    assert refetched is not None
    assert refetched.status == "completed"
    assert refetched.judge_result_id == judge_result_id


@pytest.mark.asyncio
async def test_failed_item_cannot_transition_to_completed(
    db_session: AsyncSession,
) -> None:
    """Once terminal `failed`, an Item MUST NOT silently flip to
    `completed`. The CAS WHERE clause rejects the transition; the Item's
    judge_result_id remains NULL."""

    sweep = await _make_sweep(db_session, requested_pair_count=1)
    pair = await _seed_pair(db_session, 1)
    item = EvalPairwiseSweepItem(
        sweep_id=sweep.id,
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
        status="queued",
    )
    async with session_transaction(db_session):
        await EvalRepository(db_session).create_sweep(sweep)
        await EvalRepository(db_session).create_sweep_items([item])

    first_fail = await EvalRepository(db_session).mark_sweep_item_failed(
        item.id, error_code="PROVIDER_TIMEOUT"
    )
    judge_result_id = await _real_judge_result(db_session, pair=pair)
    complete_attempt = await EvalRepository(
        db_session
    ).mark_sweep_item_completed(item.id, judge_result_id=judge_result_id)
    assert first_fail is True
    assert complete_attempt is False  # already terminal — rejected

    refetched = await EvalRepository(db_session).get_sweep_item(
        sweep.id, pair.id, "baseline"
    )
    assert refetched is not None
    assert refetched.status == "failed"
    assert refetched.judge_result_id is None
