"""PR-9c.2 pairwise_sampler tests (pure Python, no DB / IO).

Pins (per Plan v2):
* only completed+has_evidence trials eligible
* same case_id pairing only; cross-case pairing rejected
* deterministic output order
* ``pair_hash`` computed via production PR-9c.1 formula
* SAMPLER_VERSION constant frozen
* suggested_label_provider opt-in only; default None (never auto-populated)
* self-pair (baseline_trial_id == candidate_trial_id) excluded even if
  the trial set would otherwise allow it
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from evals.v2.contracts import canonical_sha256
from evals.v2.pairwise_sampler import (
    EXPORT_SCHEMA_VERSION,
    SAMPLER_VERSION,
    PairExportRecord,
    TrialBrief,
    enumerate_pair_candidates,
)


def _brief(
    case_id: str = "case-A",
    *,
    status: str = "completed",
    has_evidence: bool = True,
    request: dict[str, object] | None = None,
    plan: dict[str, object] | None = None,
) -> TrialBrief:
    if request is None:
        request = {"expect_constraint": "x"}
    if plan is None:
        plan = {"summary": "y"}
    return TrialBrief(
        trial_id=uuid4(),
        case_id=case_id,
        status=status,
        has_evidence=has_evidence,
        request_constraints=request,
        plan_projection=plan,
    )


def test_only_completed_evidence_trials_pair() -> None:
    """A pending trial or one without evidence is dropped from both sides."""

    b1 = _brief(status="completed", has_evidence=True)
    b2 = _brief(status="pending", has_evidence=True)  # filtered
    b3 = _brief(status="completed", has_evidence=False)  # filtered
    c1 = _brief(status="completed", has_evidence=True)
    c2 = _brief(status="failed", has_evidence=True)  # filtered

    records = enumerate_pair_candidates(
        baseline_experiment_id=uuid4(),
        candidate_experiment_id=uuid4(),
        trials_baseline=[b1, b2, b3],
        trials_candidate=[c1, c2],
    )
    assert len(records) == 1
    assert records[0].baseline_trial_id == b1.trial_id
    assert records[0].candidate_trial_id == c1.trial_id


def test_pairing_is_within_same_case_id_only() -> None:
    """case-A baseline trials must NOT pair with case-B candidate trials."""

    b_a = _brief("case-A")
    b_b = _brief("case-B")
    c_a = _brief("case-A")
    c_c = _brief("case-C")

    records = enumerate_pair_candidates(
        baseline_experiment_id=uuid4(),
        candidate_experiment_id=uuid4(),
        trials_baseline=[b_a, b_b],
        trials_candidate=[c_a, c_c],
    )
    case_ids = {r.case_id for r in records}
    assert case_ids == {"case-A"}  # only case-A shared


def test_deterministic_order_independent_of_input_shuffle() -> None:
    b1 = _brief("case-A", request={"x": 1}, plan={"summary": "b1"})
    b2 = _brief("case-A", request={"x": 2}, plan={"summary": "b2"})
    c1 = _brief("case-A", request={"x": 1}, plan={"summary": "c1"})

    out_order1 = enumerate_pair_candidates(
        baseline_experiment_id=uuid4(),
        candidate_experiment_id=uuid4(),
        trials_baseline=[b1, b2],
        trials_candidate=[c1],
    )
    out_order2 = enumerate_pair_candidates(
        baseline_experiment_id=uuid4(),
        candidate_experiment_id=uuid4(),
        trials_baseline=[b2, b1],
        trials_candidate=[c1],
    )
    assert [r.baseline_trial_id for r in out_order1] == [
        r.baseline_trial_id for r in out_order2
    ]


def test_pair_hash_matches_production_formula() -> None:
    """The export record's ``pair_hash`` MUST equal what PR-9c.1 production
    Pair.pair_hash() would compute — otherwise the loader will reject the
    record."""

    b = _brief("case-A", request={"r": 1}, plan={"summary": "b"})
    c = _brief("case-A", request={"r": 1}, plan={"summary": "c"})
    records = enumerate_pair_candidates(
        baseline_experiment_id=uuid4(),
        candidate_experiment_id=uuid4(),
        trials_baseline=[b],
        trials_candidate=[c],
    )
    assert len(records) == 1
    rec = records[0]
    expected_b_hash = canonical_sha256(
        {"request": b.request_constraints, "plan": b.plan_projection}
    )
    expected_c_hash = canonical_sha256(
        {"request": c.request_constraints, "plan": c.plan_projection}
    )
    expected_pair_hash = canonical_sha256({
        "schema_version": "eval-trial-pair/v1",
        "case_id": "case-A",
        "baseline_trial_id": str(b.trial_id),
        "candidate_trial_id": str(c.trial_id),
        "baseline_output_hash": expected_b_hash,
        "candidate_output_hash": expected_c_hash,
    })
    assert rec.pair_hash == expected_pair_hash
    assert rec.baseline_output_hash == expected_b_hash
    assert rec.candidate_output_hash == expected_c_hash


def test_self_pair_excluded_via_id_equality() -> None:
    """If the same trial_id accidentally shows up in both lists, it is
    filtered out — no self-pairing."""

    b = _brief("case-A")
    records = enumerate_pair_candidates(
        baseline_experiment_id=uuid4(),
        candidate_experiment_id=uuid4(),
        trials_baseline=[b],
        trials_candidate=[b],
    )
    assert records == []


def test_versions_constants_frozen() -> None:
    assert SAMPLER_VERSION == "v1"
    assert EXPORT_SCHEMA_VERSION == "pairwise-calibration-export/v1"


def test_suggested_label_provider_default_is_none() -> None:
    b = _brief("case-A")
    c = _brief("case-A")
    records = enumerate_pair_candidates(
        baseline_experiment_id=uuid4(),
        candidate_experiment_id=uuid4(),
        trials_baseline=[b],
        trials_candidate=[c],
    )
    assert records[0].suggested_label is None


def test_suggested_label_provider_opt_in_only() -> None:
    b = _brief("case-A")
    c = _brief("case-A")
    records = enumerate_pair_candidates(
        baseline_experiment_id=uuid4(),
        candidate_experiment_id=uuid4(),
        trials_baseline=[b],
        trials_candidate=[c],
        # Provider keys by pair_external_id; we don't pre-compute it
        # but checking that opt-in path engages.
        suggested_label_provider={"nonexistent": "a"},
    )
    assert records[0].suggested_label is None  # not in map → None

    # Now with the actual pair_external_id:
    actual_id = records[0].pair_external_id
    records2 = enumerate_pair_candidates(
        baseline_experiment_id=uuid4(),
        candidate_experiment_id=uuid4(),
        trials_baseline=[b],
        trials_candidate=[c],
        suggested_label_provider={actual_id: "baseline"},
    )
    assert records2[0].suggested_label == "baseline"


def test_record_is_immutable_dataclass() -> None:
    from dataclasses import FrozenInstanceError

    rec = PairExportRecord(
        schema_version=EXPORT_SCHEMA_VERSION,
        pair_external_id="x",
        case_id="case-A",
        baseline_trial_id=uuid4(),
        candidate_trial_id=uuid4(),
        baseline_output_hash="0" * 64,
        candidate_output_hash="1" * 64,
        pair_hash="2" * 64,
        suggested_label=None,
    )
    with pytest.raises(FrozenInstanceError):
        rec.case_id = "case-B"  # type: ignore[misc]
