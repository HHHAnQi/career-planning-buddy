"""PR-9c.1 EvalService.run_pairwise_judge integration tests.

Uses the real EvalService over a PostgreSQL-backed session plus a
FixturePairwiseJudge so no live LLM is needed. The trials here are real
``EvalTrial`` rows (created via create_experiment) but are NOT graded —
we attach the minimal evidence kinds the Judge reads by hand to keep the
test focused on the Judge path.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import session_transaction
from app.models.eval import EvalEvidenceItem
from app.services.evals import EvalService
from evals.v2.contracts import canonical_sha256
from evals.v2.graders.base import EvidenceKind
from evals.v2.judge import (
    DIMENSION_NAMES,
    FixturePairwiseJudge,
    PairwiseJudgeOutput,
    PairwiseJudgeResult,
)
from evals.v2.pairwise import PairwiseJudgeInput, PositionVariant
from tests.evals_v2.test_eval_repository import _config


async def _provision_trials(
    db_session: AsyncSession,
) -> tuple[UUID, UUID, str]:
    """Provision one experiment + two trials (same case). Returns
    (baseline_trial_id, candidate_trial_id, case_id) as native UUIDs
    so they can be passed straight into ``run_pairwise_judge``."""

    from evals.v2.dataset_loader import load_dataset

    _, trials = await EvalService(db_session).create_experiment(
        dataset=load_dataset(), config=_config()
    )
    case_id = trials[0].case_id
    return trials[0].id, trials[1].id, case_id


async def _attach_min_evidence_for(
    db_session: AsyncSession, trial_id: UUID, summary: str
) -> None:
    """Attach the minimal REQUIRED + RUBRIC evidence the Judge reads."""

    trial_str = str(trial_id)
    async with session_transaction(db_session):
        from app.repositories.evals import EvalRepository

        rows = [
            EvalEvidenceItem(
                trial_id=trial_id,
                kind=EvidenceKind.REQUEST_CONSTRAINTS.value,
                source_type="case",
                source_id=f"case:{trial_str}",
                content_hash=canonical_sha256(
                    {"trial": trial_str, "kind": "req", "s": summary}
                ),
                projection_json={"expect_constraint": "求职"},
                sensitivity="normal",
            ),
            EvalEvidenceItem(
                trial_id=trial_id,
                kind=EvidenceKind.PLAN_PROJECTION.value,
                source_type="run",
                source_id=f"run:{trial_str}",
                content_hash=canonical_sha256(
                    {"trial": trial_str, "kind": "plan", "s": summary}
                ),
                projection_json={"summary": summary},
                sensitivity="normal",
            ),
            EvalEvidenceItem(
                trial_id=trial_id,
                kind=EvidenceKind.RUBRIC.value,
                source_type="case",
                source_id=f"rubric:{trial_str}",
                content_hash=canonical_sha256(
                    {"trial": trial_str, "kind": "rubric", "s": summary}
                ),
                projection_json={
                    "criteria": [
                        {"criterion_id": "c1", "description": "有可执行步骤"}
                    ]
                },
                sensitivity="normal",
            ),
        ]
        await EvalRepository(db_session).create_evidence_items(rows)


def _make_output(winner: str = "a") -> PairwiseJudgeOutput:
    return PairwiseJudgeOutput(
        dimension_verdicts={n: winner for n in DIMENSION_NAMES},
        winner=winner,
        confidence="high",
        rationale="baseline wins",
    )


class _TransactionProbeJudge:
    def __init__(
        self,
        session: AsyncSession,
        delegate: FixturePairwiseJudge,
    ) -> None:
        self._session = session
        self._delegate = delegate

    @property
    def model_id(self) -> str:
        return self._delegate.model_id

    async def judge(self, prompt: PairwiseJudgeInput) -> PairwiseJudgeResult:
        assert self._session.in_transaction() is False
        return await self._delegate.judge(prompt)


def _expected_pair_hash(
    *,
    baseline_id: UUID,
    candidate_id: UUID,
    case_id: str,
    baseline_summary: str,
    candidate_summary: str,
) -> str:
    """Recompute the production ``Pair.pair_hash()`` for an expected
    (baseline, candidate) tuple given the same minimal
    REQUEST_CONSTRAINTS + PLAN_PROJECTION the test attaches.

    Per the PR-9c.1 contract, the formula is independent of
    ``comparison_group_id`` and depends only on:

        schema_version + case_id + role-trial-refs +
        baseline_output_hash + candidate_output_hash
    """

    base_str = str(baseline_id)
    cand_str = str(candidate_id)
    return canonical_sha256({
        "schema_version": "eval-trial-pair/v1",
        "case_id": case_id,
        "baseline_trial_id": base_str,
        "candidate_trial_id": cand_str,
        "baseline_output_hash": canonical_sha256(
            {"request": {"expect_constraint": "求职"},
             "plan": {"summary": baseline_summary}}
        ),
        "candidate_output_hash": canonical_sha256(
            {"request": {"expect_constraint": "求职"},
             "plan": {"summary": candidate_summary}}
        ),
    })


# ---------------------------------------------------------------- tests


@pytest.mark.asyncio
async def test_service_runs_judge_and_persists_completed_result(
    db_session: AsyncSession,
) -> None:
    baseline_id, candidate_id, case_id = await _provision_trials(db_session)
    # Need a baseline_experiment row that the trial FK can resolve. The
    # create_experiment path already wired both trials up to one experiment.
    # Build a minimal pair domain key first to feed the fixture mapping.
    pair_hash = _expected_pair_hash(
    baseline_id=baseline_id, candidate_id=candidate_id,
    case_id=case_id,
    baseline_summary="baseline plan", candidate_summary="candidate plan",
)
    await _attach_min_evidence_for(db_session, baseline_id, "baseline plan")
    await _attach_min_evidence_for(db_session, candidate_id, "candidate plan")

    judge = FixturePairwiseJudge(mapping={pair_hash: _make_output("a")})
    service = EvalService(db_session)
    run_id = uuid4()
    pair_row, result_row = await service.run_pairwise_judge(
        baseline_trial_id=baseline_id,
        candidate_trial_id=candidate_id,
        case_id=case_id,
        comparison_group_id="grp-1",
        judge_run_id=run_id,
        judge=judge,
        position_variant=PositionVariant.BASELINE,
    )
    assert pair_row.pair_hash == pair_hash
    assert result_row.judge_run_status == "completed"
    assert result_row.raw_display_winner == "a"
    assert result_row.normalized_winner == "a"
    assert result_row.position_variant == "baseline"
    assert result_row.prompt_version == "v1"
    assert result_row.confidence == "high"


@pytest.mark.asyncio
async def test_service_swapped_position_flips_normalized_winner(
    db_session: AsyncSession,
) -> None:
    baseline_id, candidate_id, case_id = await _provision_trials(db_session)
    pair_hash = _expected_pair_hash(
    baseline_id=baseline_id, candidate_id=candidate_id,
    case_id=case_id,
    baseline_summary="baseline plan", candidate_summary="candidate plan",
)
    await _attach_min_evidence_for(db_session, baseline_id, "baseline plan")
    await _attach_min_evidence_for(db_session, candidate_id, "candidate plan")

    judge = FixturePairwiseJudge(mapping={pair_hash: _make_output("a")})
    pair_row, result_row = await EvalService(db_session).run_pairwise_judge(
        baseline_trial_id=baseline_id,
        candidate_trial_id=candidate_id,
        case_id=case_id,
        comparison_group_id="grp-swap",
        judge_run_id=uuid4(),
        judge=judge,
        position_variant=PositionVariant.SWAPPED,
    )
    # SWAPPED + raw "a" → normalized "b"
    assert result_row.raw_display_winner == "a"
    assert result_row.normalized_winner == "b"
    assert result_row.position_variant == "swapped"


@pytest.mark.asyncio
async def test_service_fail_closed_unmapped_pair_persists_invalid(
    db_session: AsyncSession,
) -> None:
    baseline_id, candidate_id, case_id = await _provision_trials(db_session)
    await _attach_min_evidence_for(db_session, baseline_id, "baseline")
    await _attach_min_evidence_for(db_session, candidate_id, "candidate")

    # Empty mapping → every pair fails closed with invalid_structured_output.
    judge = FixturePairwiseJudge(mapping={})
    _, result_row = await EvalService(db_session).run_pairwise_judge(
        baseline_trial_id=baseline_id,
        candidate_trial_id=candidate_id,
        case_id=case_id,
        comparison_group_id="grp-fail",
        judge_run_id=uuid4(),
        judge=judge,
    )
    assert result_row.judge_run_status == "invalid_structured_output"
    assert result_row.raw_display_winner is None
    assert result_row.normalized_winner is None


@pytest.mark.asyncio
async def test_service_calls_judge_after_read_transaction_closes(
    db_session: AsyncSession,
) -> None:
    baseline_id, candidate_id, case_id = await _provision_trials(db_session)
    await _attach_min_evidence_for(db_session, baseline_id, "baseline")
    await _attach_min_evidence_for(db_session, candidate_id, "candidate")
    pair_hash = _expected_pair_hash(
        baseline_id=baseline_id,
        candidate_id=candidate_id,
        case_id=case_id,
        baseline_summary="baseline",
        candidate_summary="candidate",
    )
    judge = _TransactionProbeJudge(
        db_session,
        FixturePairwiseJudge(mapping={pair_hash: _make_output("a")}),
    )

    _, result = await EvalService(db_session).run_pairwise_judge(
        baseline_trial_id=baseline_id,
        candidate_trial_id=candidate_id,
        case_id=case_id,
        comparison_group_id="grp-no-long-tx",
        judge_run_id=uuid4(),
        judge=judge,
    )

    assert result.judge_run_status == "completed"


@pytest.mark.asyncio
async def test_service_second_run_for_same_pair_reuses_pair_row(
    db_session: AsyncSession,
) -> None:
    baseline_id, candidate_id, case_id = await _provision_trials(db_session)
    pair_hash = _expected_pair_hash(
    baseline_id=baseline_id, candidate_id=candidate_id,
    case_id=case_id,
    baseline_summary="baseline", candidate_summary="candidate",
)
    await _attach_min_evidence_for(db_session, baseline_id, "baseline")
    await _attach_min_evidence_for(db_session, candidate_id, "candidate")

    judge_a = FixturePairwiseJudge(mapping={pair_hash: _make_output("a")})
    judge_b = FixturePairwiseJudge(mapping={pair_hash: _make_output("b")})
    service = EvalService(db_session)

    pair_a, result_a = await service.run_pairwise_judge(
        baseline_trial_id=baseline_id,
        candidate_trial_id=candidate_id,
        case_id=case_id,
        comparison_group_id="grp-reuse",
        judge_run_id=uuid4(),
        judge=judge_a,
        position_variant=PositionVariant.BASELINE,
    )
    pair_b, result_b = await service.run_pairwise_judge(
        baseline_trial_id=baseline_id,
        candidate_trial_id=candidate_id,
        case_id=case_id,
        comparison_group_id="grp-reuse",
        judge_run_id=uuid4(),
        judge=judge_b,
        position_variant=PositionVariant.BASELINE,
    )
    # Same pair row, different result row.
    assert pair_a.id == pair_b.id
    assert result_a.id != result_b.id
    assert result_a.normalized_winner == "a"
    assert result_b.normalized_winner == "b"


@pytest.mark.asyncio
async def test_service_run_pair_writes_two_results_for_position_audit(
    db_session: AsyncSession,
) -> None:
    """A typical position-consistency study: run the same pair at both
    orientations, persist both rows, then list-by-pair returns 2 rows
    with the same pair_id but distinct winner/normalized_winner pairs."""

    from app.repositories.evals import EvalRepository

    baseline_id, candidate_id, case_id = await _provision_trials(db_session)
    pair_hash = _expected_pair_hash(
    baseline_id=baseline_id, candidate_id=candidate_id,
    case_id=case_id,
    baseline_summary="baseline", candidate_summary="candidate",
)
    await _attach_min_evidence_for(db_session, baseline_id, "baseline")
    await _attach_min_evidence_for(db_session, candidate_id, "candidate")

    # Position-biased Judge: always picks "a" (always the first slot).
    judge = FixturePairwiseJudge(mapping={pair_hash: _make_output("a")})
    service = EvalService(db_session)
    pair_regular, _ = await service.run_pairwise_judge(
        baseline_trial_id=baseline_id,
        candidate_trial_id=candidate_id,
        case_id=case_id,
        comparison_group_id="grp-consist",
        judge_run_id=uuid4(),
        judge=judge,
        position_variant=PositionVariant.BASELINE,
    )
    await service.run_pairwise_judge(
        baseline_trial_id=baseline_id,
        candidate_trial_id=candidate_id,
        case_id=case_id,
        comparison_group_id="grp-consist",
        judge_run_id=uuid4(),
        judge=judge,
        position_variant=PositionVariant.SWAPPED,
    )

    async with session_transaction(db_session):
        rows = await EvalRepository(db_session).list_judge_results_by_pair(
            pair_regular.id
        )
    assert len(rows) == 2
    by_position = {r.position_variant: r for r in rows}
    # Both rows have raw_display_winner="a", but normalized flips under swap.
    assert by_position["baseline"].normalized_winner == "a"
    assert by_position["swapped"].normalized_winner == "b"


@pytest.mark.asyncio
async def test_same_pair_reuses_trial_pair_row_across_comparison_groups(
    db_session: AsyncSession,
) -> None:
    """PR-9c.2-critical: when the same baseline/candidate trial pair is
    re-Judged under a NEW ``comparison_group_id`` (e.g., a new Judge
    prompt model), the same ``EvalTrialPair`` row MUST be reused, with
    a brand new ``EvalPairwiseJudgeResult`` row carrying the new group.

    This guards the contract that ``pair_hash`` (and therefore the Pair
    row) is the stable business identity, while ``comparison_group_id``
    is a per-execution attribute stored on the Result row."""

    baseline_id, candidate_id, case_id = await _provision_trials(db_session)
    pair_hash = _expected_pair_hash(
        baseline_id=baseline_id, candidate_id=candidate_id,
        case_id=case_id,
        baseline_summary="baseline", candidate_summary="candidate",
    )
    await _attach_min_evidence_for(db_session, baseline_id, "baseline")
    await _attach_min_evidence_for(db_session, candidate_id, "candidate")

    judge = FixturePairwiseJudge(mapping={pair_hash: _make_output("a")})
    service = EvalService(db_session)

    # First comparison_group_id (simulating prompt v1 + swap run).
    pair_g1, _ = await service.run_pairwise_judge(
        baseline_trial_id=baseline_id,
        candidate_trial_id=candidate_id,
        case_id=case_id,
        comparison_group_id="group-v1",
        judge_run_id=uuid4(),
        judge=judge,
        position_variant=PositionVariant.BASELINE,
    )

    # Second comparison_group_id (simulating a re-evaluation with prompt v2
    # or a fresh swap run). Pair row MUST be the same; results are distinct.
    pair_g2, _ = await service.run_pairwise_judge(
        baseline_trial_id=baseline_id,
        candidate_trial_id=candidate_id,
        case_id=case_id,
        comparison_group_id="group-v2",
        judge_run_id=uuid4(),
        judge=judge,
        position_variant=PositionVariant.BASELINE,
    )

    assert pair_g1.id == pair_g2.id, (
        "Pair row MUST be reused when outputs are unchanged — comparison_group_id "
        "is a per-execution field"
    )
    assert pair_g1.pair_hash == pair_g2.pair_hash == pair_hash

    # Two distinct Result rows hang off the same Pair, one per group.
    async with session_transaction(db_session):
        from app.repositories.evals import EvalRepository

        results = await EvalRepository(db_session).list_judge_results_by_pair(
            pair_g1.id
        )
    assert len(results) == 2
    assert sorted(r.comparison_group_id for r in results) == [
        "group-v1", "group-v2"
    ]
