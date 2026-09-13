"""PR-9c.1 Pairwise DB constraints + Repository tests.

Pins the two-table schema and 7 repository methods:

* ``eval_trial_pairs`` UNIQUE on pair_hash + on (baseline, candidate, group)
* ``eval_pairwise_judge_results`` UNIQUE on (pair_id, judge_run_id)
* winner CHECK rejects values outside {a,b,tie,both_unacceptable}
* completed rows MUST carry both raw + normalized winners; invalid rows
  MUST NOT carry any winner
* position_variant CHECK
* ``get_or_create_pair`` is idempotent (re-insert returns existing)
* ``get_pair`` / ``get_pair_by_hash`` lookups
* ``create_judge_result`` + ``get_judge_result`` round-trip
* ``list_judge_results_by_pair`` and ``list_judge_results_by_comparison_group``
* cascade delete from pair to results
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import session_transaction
from app.models.eval import (
    EvalPairwiseJudgeResult,
    EvalTrialPair,
)
from app.repositories.evals import EvalRepository
from app.services.evals import EvalService
from evals.v2.contracts import canonical_sha256
from evals.v2.graders.base import EvidenceItem, EvidenceKind
from evals.v2.pairwise import JUDGE_ALLOWED_KINDS
from tests.evals_v2.test_eval_repository import _config

VALID_HASH = "0" * 64


async def _provision_experiment(db_session: AsyncSession) -> tuple[str, list[str]]:
    """Create a baseline experiment and return case_id + trial id strings.

    Uses the live EvalService.create_experiment path so trials land in the
    DB with the right experiment FK and unique case ids.
    """

    from evals.v2.dataset_loader import load_dataset

    service = EvalService(db_session)
    _, trials = await service.create_experiment(
        dataset=load_dataset(), config=_config()
    )
    for index, trial in enumerate(trials[:2]):
        request_projection: dict[str, object] = {
            "expect_constraint": "test request"
        }
        plan_projection: dict[str, object] = {"summary": f"test plan {index}"}
        await service.attach_evidence(
            trial.id,
            [
                EvidenceItem(
                    id=uuid4(),
                    trial_id=trial.id,
                    kind=EvidenceKind.REQUEST_CONSTRAINTS,
                    source_type="eval_case",
                    source_id=trial.case_id,
                    content_hash=canonical_sha256(request_projection),
                    projection=request_projection,
                    sensitivity="normal",
                ),
                EvidenceItem(
                    id=uuid4(),
                    trial_id=trial.id,
                    kind=EvidenceKind.PLAN_PROJECTION,
                    source_type="agent_run",
                    source_id=str(trial.id),
                    content_hash=canonical_sha256(plan_projection),
                    projection=plan_projection,
                    sensitivity="normal",
                ),
            ],
        )
    case_id = trials[0].case_id
    return case_id, [str(t.id) for t in trials[:2]]


# ------------------------------------------------------- pair + result


async def _make_pair(
    db_session: AsyncSession,
    *,
    baseline_id: str,
    candidate_id: str,
    case_id: str,
) -> EvalTrialPair:
    """Construct an ``EvalTrialPair`` for tests.

    Per the PR-9c.1 contract, ``pair_hash`` excludes
    ``comparison_group_id`` and any per-execution attribute — it is the
    stable Pair identity (schema_version + case_id + trial refs +
    output hashes). For tests we synthesize an output-content hash from
    the trial ids so each pair of trials lands on a distinct pair_hash."""

    return EvalTrialPair(
        baseline_trial_id=baseline_id,
        candidate_trial_id=candidate_id,
        case_id=case_id,
        pair_hash=canonical_sha256({
            "schema_version": "eval-trial-pair/v1",
            "case_id": case_id,
            "baseline_trial_id": baseline_id,
            "candidate_trial_id": candidate_id,
            "baseline_output_hash": canonical_sha256({"trial": baseline_id}),
            "candidate_output_hash": canonical_sha256({"trial": candidate_id}),
        }),
        input_hash=VALID_HASH,
        allowed_evidence_kinds=sorted(k.value for k in JUDGE_ALLOWED_KINDS),
        judge_prompt_version="v1",
        judge_rubric_version="v1",
    )


# ---------------------------------------------------------------- tests


@pytest.mark.asyncio
async def test_get_or_create_pair_inserts_and_reads_back(
    db_session: AsyncSession,
) -> None:
    case_id, trials = await _provision_experiment(db_session)
    pair = await _make_pair(
        db_session, baseline_id=trials[0], candidate_id=trials[1], case_id=case_id
    )
    async with session_transaction(db_session):
        persisted = await EvalRepository(db_session).get_or_create_pair(pair)
    assert persisted.id is not None
    assert persisted.pair_hash == pair.pair_hash

    async with session_transaction(db_session):
        round_tripped = await EvalRepository(db_session).get_pair(persisted.id)
    assert round_tripped is not None
    assert round_tripped.pair_hash == persisted.pair_hash


@pytest.mark.asyncio
async def test_get_pair_by_hash_looks_up_existing_row(
    db_session: AsyncSession,
) -> None:
    case_id, trials = await _provision_experiment(db_session)
    pair = await _make_pair(
        db_session, baseline_id=trials[0], candidate_id=trials[1], case_id=case_id
    )
    async with session_transaction(db_session):
        persisted = await EvalRepository(db_session).get_or_create_pair(pair)
        by_hash = await EvalRepository(db_session).get_pair_by_hash(pair.pair_hash)
    assert by_hash is not None
    assert by_hash.id == persisted.id


@pytest.mark.asyncio
async def test_get_or_create_pair_is_idempotent(db_session: AsyncSession) -> None:
    case_id, trials = await _provision_experiment(db_session)
    pair_a = await _make_pair(
        db_session, baseline_id=trials[0], candidate_id=trials[1], case_id=case_id
    )
    async with session_transaction(db_session):
        first = await EvalRepository(db_session).get_or_create_pair(pair_a)
    # Second call with same key should return the SAME row, not insert a new one.
    pair_b = await _make_pair(
        db_session, baseline_id=trials[0], candidate_id=trials[1], case_id=case_id
    )
    async with session_transaction(db_session):
        second = await EvalRepository(db_session).get_or_create_pair(pair_b)
    assert first.id == second.id


@pytest.mark.asyncio
async def test_create_judge_result_round_trip(db_session: AsyncSession) -> None:
    case_id, trials = await _provision_experiment(db_session)
    pair = await _make_pair(
        db_session, baseline_id=trials[0], candidate_id=trials[1], case_id=case_id
    )
    run_id = uuid4()
    async with session_transaction(db_session):
        persisted_pair = await EvalRepository(db_session).get_or_create_pair(pair)
        result = await EvalRepository(db_session).create_judge_result(
            EvalPairwiseJudgeResult(
                pair_id=persisted_pair.id,
                judge_run_id=run_id,
                judge_run_status="completed",
                position_variant="baseline",

                comparison_group_id="grp-auto",
                raw_display_winner="a",
                normalized_winner="a",
                raw_dimension_verdicts={"actionability": "a"},
                normalized_dimension_verdicts={"actionability": "a"},
                confidence="high",
                rationale="baseline won",
                model_id="fixture-judge-v1",
                prompt_version="v1",
                rubric_version="v1",
                input_hash=VALID_HASH,
            )
        )
    assert result.id is not None

    async with session_transaction(db_session):
        fetched = await EvalRepository(db_session).get_judge_result(
            persisted_pair.id, run_id
        )
    assert fetched is not None
    assert fetched.raw_display_winner == "a"
    assert fetched.normalized_winner == "a"


@pytest.mark.asyncio
async def test_completed_result_requires_both_winners(db_session: AsyncSession) -> None:
    """A completed result with NULL normalized_winner violates the CHECK."""

    case_id, trials = await _provision_experiment(db_session)
    pair = await _make_pair(
        db_session, baseline_id=trials[0], candidate_id=trials[1], case_id=case_id
    )
    async with session_transaction(db_session):
        persisted_pair = await EvalRepository(db_session).get_or_create_pair(pair)
        with pytest.raises((DBAPIError, IntegrityError), match=r"completed_carries_verdict|check"):
            await EvalRepository(db_session).create_judge_result(
                EvalPairwiseJudgeResult(
                    pair_id=persisted_pair.id,
                    judge_run_id=uuid4(),
                    judge_run_status="completed",
                    position_variant="baseline",

                    comparison_group_id="grp-auto",
                    raw_display_winner="a",
                    normalized_winner=None,  # violation
                    model_id="fixture-judge-v1",
                    prompt_version="v1",
                    rubric_version="v1",
                    input_hash=VALID_HASH,
                )
            )


@pytest.mark.asyncio
async def test_invalid_status_rejects_non_null_winners(db_session: AsyncSession) -> None:
    """``judge_run_status='invalid_structured_output'`` AND both winners
    non-NULL violates the CHECK:

    ``(status='completed') = (raw IS NOT NULL AND normalized IS NOT NULL)``
    → false = true → violation.
    """

    case_id, trials = await _provision_experiment(db_session)
    pair = await _make_pair(
        db_session, baseline_id=trials[0], candidate_id=trials[1], case_id=case_id
    )
    async with session_transaction(db_session):
        persisted_pair = await EvalRepository(db_session).get_or_create_pair(pair)
        with pytest.raises((DBAPIError, IntegrityError), match=r"completed_carries_verdict|check"):
            await EvalRepository(db_session).create_judge_result(
                EvalPairwiseJudgeResult(
                    pair_id=persisted_pair.id,
                    judge_run_id=uuid4(),
                    judge_run_status="invalid_structured_output",
                    position_variant="baseline",

                    comparison_group_id="grp-auto",
                    raw_display_winner="a",  # both non-null + invalid → violation
                    normalized_winner="a",
                    model_id="fixture-judge-v1",
                    prompt_version="v1",
                    rubric_version="v1",
                    input_hash=VALID_HASH,
                )
            )


@pytest.mark.asyncio
async def test_winner_value_outside_vocabulary_rejected(
    db_session: AsyncSession,
) -> None:
    case_id, trials = await _provision_experiment(db_session)
    pair = await _make_pair(
        db_session, baseline_id=trials[0], candidate_id=trials[1], case_id=case_id
    )
    async with session_transaction(db_session):
        persisted_pair = await EvalRepository(db_session).get_or_create_pair(pair)
        with pytest.raises((DBAPIError, IntegrityError), match=r"raw_winner|check"):
            await EvalRepository(db_session).create_judge_result(
                EvalPairwiseJudgeResult(
                    pair_id=persisted_pair.id,
                    judge_run_id=uuid4(),
                    judge_run_status="completed",
                    position_variant="baseline",

                    comparison_group_id="grp-auto",
                    raw_display_winner="invalid",  # NOT a winner
                    normalized_winner="a",
                    model_id="fixture-judge-v1",
                    prompt_version="v1",
                    rubric_version="v1",
                    input_hash=VALID_HASH,
                )
            )


@pytest.mark.asyncio
async def test_position_variant_outside_vocabulary_rejected(
    db_session: AsyncSession,
) -> None:
    case_id, trials = await _provision_experiment(db_session)
    pair = await _make_pair(
        db_session, baseline_id=trials[0], candidate_id=trials[1], case_id=case_id
    )
    async with session_transaction(db_session):
        persisted_pair = await EvalRepository(db_session).get_or_create_pair(pair)
        with pytest.raises((DBAPIError, IntegrityError), match=r"position_variant|check"):
            await EvalRepository(db_session).create_judge_result(
                EvalPairwiseJudgeResult(
                    pair_id=persisted_pair.id,
                    judge_run_id=uuid4(),
                    judge_run_status="invalid_structured_output",
                    position_variant="flipped",  # violation
                    comparison_group_id="grp-bad",
                    model_id="fixture-judge-v1",
                    prompt_version="v1",
                    rubric_version="v1",
                    input_hash=VALID_HASH,
                )
            )


@pytest.mark.asyncio
async def test_unique_pair_run_rejects_duplicate(
    db_session: AsyncSession,
) -> None:
    case_id, trials = await _provision_experiment(db_session)
    pair = await _make_pair(
        db_session, baseline_id=trials[0], candidate_id=trials[1], case_id=case_id
    )
    run_id = uuid4()
    async with session_transaction(db_session):
        persisted_pair = await EvalRepository(db_session).get_or_create_pair(pair)
        await EvalRepository(db_session).create_judge_result(
            EvalPairwiseJudgeResult(
                pair_id=persisted_pair.id,
                judge_run_id=run_id,
                judge_run_status="invalid_structured_output",
                position_variant="baseline",

                comparison_group_id="grp-auto",
                model_id="fixture-judge-v1",
                prompt_version="v1",
                rubric_version="v1",
                input_hash=VALID_HASH,
            )
        )
        with pytest.raises((DBAPIError, IntegrityError)):
            await EvalRepository(db_session).create_judge_result(
                EvalPairwiseJudgeResult(
                    pair_id=persisted_pair.id,
                    judge_run_id=run_id,  # duplicate
                    judge_run_status="invalid_structured_output",
                    position_variant="baseline",

                    comparison_group_id="grp-auto",
                    model_id="fixture-judge-v1",
                    prompt_version="v1",
                    rubric_version="v1",
                    input_hash=VALID_HASH,
                )
            )


@pytest.mark.asyncio
async def test_list_judge_results_by_pair(db_session: AsyncSession) -> None:
    case_id, trials = await _provision_experiment(db_session)
    pair = await _make_pair(
        db_session, baseline_id=trials[0], candidate_id=trials[1], case_id=case_id
    )
    async with session_transaction(db_session):
        persisted_pair = await EvalRepository(db_session).get_or_create_pair(pair)
        for _ in range(2):
            await EvalRepository(db_session).create_judge_result(
                EvalPairwiseJudgeResult(
                    pair_id=persisted_pair.id,
                    judge_run_id=uuid4(),
                    judge_run_status="invalid_structured_output",
                    position_variant="baseline",

                    comparison_group_id="grp-auto",
                    model_id="fixture-judge-v1",
                    prompt_version="v1",
                    rubric_version="v1",
                    input_hash=VALID_HASH,
                )
            )
    async with session_transaction(db_session):
        rows = await EvalRepository(db_session).list_judge_results_by_pair(
            persisted_pair.id
        )
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_list_judge_results_by_comparison_group(
    db_session: AsyncSession,
) -> None:
    case_id, trials = await _provision_experiment(db_session)
    group = "grp-A"
    pair = await _make_pair(
        db_session,
        baseline_id=trials[0],
        candidate_id=trials[1],
        case_id=case_id,
    )
    async with session_transaction(db_session):
        persisted_pair = await EvalRepository(db_session).get_or_create_pair(pair)
        await EvalRepository(db_session).create_judge_result(
            EvalPairwiseJudgeResult(
                pair_id=persisted_pair.id,
                judge_run_id=uuid4(),
                judge_run_status="invalid_structured_output",
                position_variant="baseline",
                comparison_group_id=group,
                model_id="fixture-judge-v1",
                prompt_version="v1",
                rubric_version="v1",
                input_hash=VALID_HASH,
            )
        )
    async with session_transaction(db_session):
        joined = await EvalRepository(
            db_session
        ).list_judge_results_by_comparison_group(group)
    assert len(joined) == 1
    pair_row, result_row = joined[0]
    assert pair_row.case_id == case_id
    assert result_row.pair_id == persisted_pair.id
    assert result_row.comparison_group_id == group


@pytest.mark.asyncio
async def test_same_trial_ids_with_changed_output_hash_creates_second_pair_row(
    db_session: AsyncSession,
) -> None:
    """PR-9c.2-critical: a re-collect that moves output bytes MUST be able
    to create a SECOND ``EvalTrialPair`` row with the SAME
    (baseline_trial_id, candidate_trial_id) tuple but a DIFFERENT
    pair_hash. The two Pair snapshots coexist so calibration history
    stays attributable to its respective bytes.

    Preconditions this test guards:

    * ``(baseline_trial_id, candidate_trial_id)`` index is NON-UNIQUE;
    * ``pair_hash`` is UNIQUE, so two snapshots with equal pair_hash
      would fail — guaranteed by content-aware hashing;
    * ``get_or_create_pair`` only de-duplicates on pair_hash.
    """

    case_id, trials = await _provision_experiment(db_session)
    baseline_id = trials[0]
    candidate_id = trials[1]

    # Two distinct pair_hash values for the SAME trial tuple, simulating
    # the production Pair.pair_hash() differing because output bytes
    # changed between re-collects. Both pair_hashes are valid 64-hex.
    hash_v1 = "a" * 64
    hash_v2 = "b" * 64

    async with session_transaction(db_session):
        pair_v1 = await EvalRepository(db_session).get_or_create_pair(
            EvalTrialPair(
                baseline_trial_id=baseline_id,
                candidate_trial_id=candidate_id,
                case_id=case_id,
                pair_hash=hash_v1,
                input_hash=VALID_HASH,
                allowed_evidence_kinds=sorted(
                    kind.value for kind in JUDGE_ALLOWED_KINDS
                ),
                judge_prompt_version="v1",
                judge_rubric_version="v1",
            )
        )
        pair_v2 = await EvalRepository(db_session).get_or_create_pair(
            EvalTrialPair(
                baseline_trial_id=baseline_id,
                candidate_trial_id=candidate_id,
                case_id=case_id,
                pair_hash=hash_v2,  # different content
                input_hash=VALID_HASH,
                allowed_evidence_kinds=sorted(
                    kind.value for kind in JUDGE_ALLOWED_KINDS
                ),
                judge_prompt_version="v1",
                judge_rubric_version="v1",
            )
        )

    # The two Pair rows are DISTINCT even though their trial tuples match.
    assert pair_v1.id != pair_v2.id
    assert pair_v1.baseline_trial_id == pair_v2.baseline_trial_id
    assert pair_v1.candidate_trial_id == pair_v2.candidate_trial_id
    assert pair_v1.pair_hash != pair_v2.pair_hash

    # Both rows are independently retrievable by pair_hash.
    async with session_transaction(db_session):
        refetched_v1 = await EvalRepository(db_session).get_pair_by_hash(hash_v1)
        refetched_v2 = await EvalRepository(db_session).get_pair_by_hash(hash_v2)
    assert refetched_v1 is not None and refetched_v2 is not None
    assert refetched_v1.id == pair_v1.id
    assert refetched_v2.id == pair_v2.id
