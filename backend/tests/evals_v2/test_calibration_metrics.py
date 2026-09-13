"""PR-9c.2 calibration_metrics tests (pure Python, no DB / IO).

Pins (per Plan v2 + supplementary constraints):
* unify_winner_to_baseline_candidate is total on the 4-value vocabulary
* flat_agreement_rate: empty / perfect / partial / mismatched length
* cohens_kappa: perfect / chance (1-p_e==0 returns None) / category
  marginals with one zero are NOT degenerate
* per_dimension variants
* inter_rater_* computed on common items only, NOT on disagreement subset
  (per user revision #6)
* compute_calibration_status three-state machine:
  - insufficient when counts below thresholds OR None metrics
  - failing when agreement < 0.70 OR position_bias > 0.15
  - passing only when everything is satisfied
  - usage_mode is diagnostic_only unless passing (revision #9: no grey band)
* derive_pair_consensus_status covers single/consensus/dispute/adjudicated
* THRESHOLDS + CALIBRATION_POLICY_VERSION constants frozen
"""

from __future__ import annotations

import pytest

from evals.v2.calibration_metrics import (
    CALIBRATION_POLICY_VERSION,
    DIMENSION_NAMES,
    THRESHOLDS,
    BaselineCandidateLabel,  # noqa: F401  (sanity-import)
    MetricResult,
    ReviewerAnnotation,
    ReviewerDimensionVerdict,
    cohens_kappa,
    compute_calibration_status,
    derive_pair_consensus_status,
    flat_agreement_rate,
    inter_rater_cohens_kappa,
    inter_rater_flat_agreement,
    per_dimension_cohens_kappa,
    per_dimension_flat_agreement,
    unify_dimensions,
    unify_winner_to_baseline_candidate,
)

# ----------------------------------------------------- unify vocabulary


def test_unify_a_b_to_baseline_candidate() -> None:
    assert unify_winner_to_baseline_candidate("a") == "baseline"
    assert unify_winner_to_baseline_candidate("b") == "candidate"
    assert unify_winner_to_baseline_candidate("tie") == "tie"
    assert unify_winner_to_baseline_candidate("both_unacceptable") == "both_unacceptable"


def test_unify_dimensions_applies_per_dim() -> None:
    raw = {
        "actionability": "a",
        "alignment": "b",
        "personalization": "tie",
        "clarity": "both_unacceptable",
        "consistency": "a",
    }
    out = unify_dimensions(raw)  # type: ignore[arg-type]
    assert out["actionability"] == "baseline"
    assert out["alignment"] == "candidate"
    assert out["personalization"] == "tie"
    assert out["clarity"] == "both_unacceptable"
    assert out["consistency"] == "baseline"


# ------------------------------------------------------- flat agreement


def test_flat_agreement_empty_returns_none() -> None:
    assert flat_agreement_rate([], []) == MetricResult(value=None, sample_count=0)


def test_flat_agreement_length_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="length mismatch"):
        flat_agreement_rate(["baseline"], ["baseline", "candidate"])


def test_flat_agreement_perfect_match() -> None:
    labels: list[BaselineCandidateLabel] = ["baseline", "candidate", "tie", "both_unacceptable"]
    result = flat_agreement_rate(labels, labels)
    assert result.value == 1.0
    assert result.sample_count == 4


def test_flat_agreement_partial_match() -> None:
    judge: list[BaselineCandidateLabel] = ["baseline", "candidate", "tie"]
    human: list[BaselineCandidateLabel] = ["baseline", "baseline", "tie"]
    result = flat_agreement_rate(judge, human)
    assert result.value == pytest.approx(2 / 3)
    assert result.sample_count == 3


# --------------------------------------------------------- cohens kappa


def test_cohens_kappa_empty_returns_none() -> None:
    assert cohens_kappa([], []) == MetricResult(value=None, sample_count=0)


def test_cohens_kappa_length_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="length mismatch"):
        cohens_kappa(["baseline"], ["baseline", "candidate"])


def test_cohens_kappa_perfect_agreement_is_one() -> None:
    labels: list[BaselineCandidateLabel] = [
        "baseline", "candidate", "tie", "both_unacceptable",
        "baseline", "candidate",
    ]
    result = cohens_kappa(labels, labels)
    assert result.value == 1.0
    assert result.sample_count == 6


def test_cohens_kappa_returns_none_when_chance_total() -> None:
    """Both raters put 100% on the SAME category → p_e == 1 → 1-p_e == 0.

    Per user Q4: return MetricResult(None, N)."""

    judge: list[BaselineCandidateLabel] = ["baseline", "baseline", "baseline"]
    human: list[BaselineCandidateLabel] = ["baseline", "baseline", "baseline"]
    result = cohens_kappa(judge, human)
    assert result.value is None
    assert result.sample_count == 3


def test_cohens_kappa_one_zero_marginal_is_not_degenerate() -> None:
    """Judge uses only baseline/candidate; Human uses only baseline.
    ``tie`` has marginal 0 on both sides — but ``1 - p_e != 0`` because
    marginals differ on baseline/candidate. κ is computable."""

    judge: list[BaselineCandidateLabel] = ["baseline", "candidate", "baseline"]
    human: list[BaselineCandidateLabel] = ["baseline", "baseline", "baseline"]
    result = cohens_kappa(judge, human)
    assert result.value is not None  # not degenerate
    assert -0.5 < result.value < 0.5  # weak agreement is expected shape


def test_cohens_kappa_extreme_disagreement_is_negative() -> None:
    judge: list[BaselineCandidateLabel] = ["baseline", "baseline", "candidate", "candidate"]
    human: list[BaselineCandidateLabel] = ["candidate", "candidate", "baseline", "baseline"]
    result = cohens_kappa(judge, human)
    assert result.value is not None
    assert result.value < 0  # worse than chance


# --------------------------------------------------- per-dimension


def _dim_dict(
    value: str, *, override: dict[str, str] | None = None
) -> dict[str, ReviewerDimensionVerdict]:
    out: dict[str, ReviewerDimensionVerdict] = {d: value for d in DIMENSION_NAMES}  # type: ignore[misc]
    if override:
        for k, v in override.items():
            out[k] = v  # type: ignore[assignment]
    return out


def test_per_dimension_flat_agreement_returns_one_per_dim() -> None:
    judge_dims = [_dim_dict("baseline"), _dim_dict("candidate")]
    human_dims = [_dim_dict("baseline"), _dim_dict("candidate")]
    result = per_dimension_flat_agreement(judge_dims, human_dims)
    assert set(result.keys()) == set(DIMENSION_NAMES)
    for dim in DIMENSION_NAMES:
        assert result[dim].value == 1.0


def test_per_dimension_cohens_kappa_perfect() -> None:
    judge_dims = [_dim_dict("baseline"), _dim_dict("candidate")]
    result = per_dimension_cohens_kappa(judge_dims, judge_dims)
    assert all(
        result[dim].value == 1.0 for dim in DIMENSION_NAMES
    )


def test_per_dimension_flat_agreement_mixed() -> None:
    judge_dims = [_dim_dict("baseline"), _dim_dict("candidate")]
    human_dims = [_dim_dict("baseline"), _dim_dict("baseline")]
    result = per_dimension_flat_agreement(judge_dims, human_dims)
    for dim in DIMENSION_NAMES:
        # 1 match out of 2 = 0.5
        assert result[dim].value == 0.5


# -------------------------------------------------------- inter-rater


def _ann(
    reviewer_id: str,
    pair_id: str,
    label: BaselineCandidateLabel,
    is_adj: bool = False,
) -> ReviewerAnnotation:
    return ReviewerAnnotation(
        reviewer_id=reviewer_id,
        pair_id=pair_id,
        label=label,
        is_adjudication=is_adj,
    )


def test_inter_rater_flat_agreement_on_common_pairs_only() -> None:
    """Pairs annotated by only ONE primary are excluded. Adjudication
    rows are excluded (they are third-person verdicts).

    Setup:
      P1: Alice=baseline, Bob=baseline  → agree
      P2: Alice=candidate, Bob=baseline → disagree
      P3: Alice only                    → excluded
      P4: Alice=candidate, Bob=candidate, Carol (adjudicator) → both Alice+Bob
           primaries; agreement. Adjudicator row excluded.

    Common primary-primary set: P1 + P2 + P4 = 3 pairs.
    Agreements: P1 + P4 = 2. Rate = 2/3."""

    annotations = [
        _ann("alice", "P1", "baseline"),
        _ann("bob", "P1", "baseline"),
        _ann("alice", "P2", "candidate"),
        _ann("bob", "P2", "baseline"),
        _ann("alice", "P3", "baseline"),
        _ann("carol", "P4", "baseline", is_adj=True),
        _ann("alice", "P4", "candidate"),
        _ann("bob", "P4", "candidate"),
    ]
    result = inter_rater_flat_agreement(annotations)
    assert result.value == pytest.approx(2 / 3)
    assert result.sample_count == 3


def test_inter_rater_flat_agreement_empty_when_no_common() -> None:
    annotations = [
        _ann("alice", "P1", "baseline"),
        _ann("bob", "P2", "baseline"),
    ]
    result = inter_rater_flat_agreement(annotations)
    assert result == MetricResult(value=None, sample_count=0)


def test_inter_rater_cohens_kappa_per_reviewer_pair() -> None:
    annotations = [
        _ann("alice", "P1", "baseline"),
        _ann("bob", "P1", "baseline"),
        _ann("alice", "P2", "candidate"),
        _ann("bob", "P2", "candidate"),
    ]
    result = inter_rater_cohens_kappa(annotations)
    # Only one reviewer pair (alice, bob)
    assert len(result) == 1
    rp_key = ("alice", "bob")
    assert rp_key in result
    # Perfect agreement → κ = None (1-p_e == 0, single category each side
    # actually 50/50 so p_e = 0.5, not degenerate; perfect → κ = 1.0)
    # 2 common pairs, both agree, 50/50 marginals on each side → κ = 1.0
    assert result[rp_key].value == 1.0


# --------------------------------------------- calibration status


def test_calibration_status_insufficient_low_human_count() -> None:
    result = compute_calibration_status(
        agreement=0.95, position_bias=0.05,
        valid_human_pair_count=50,  # below 100
        position_pair_count=200,
    )
    assert result.calibration_status == "insufficient"
    assert result.usage_mode == "diagnostic_only"


def test_calibration_status_insufficient_low_position_count() -> None:
    result = compute_calibration_status(
        agreement=0.95, position_bias=0.05,
        valid_human_pair_count=200,
        position_pair_count=50,  # below 100
    )
    assert result.calibration_status == "insufficient"


def test_calibration_status_insufficient_when_agreement_is_none() -> None:
    result = compute_calibration_status(
        agreement=None, position_bias=0.05,
        valid_human_pair_count=200, position_pair_count=200,
    )
    assert result.calibration_status == "insufficient"


def test_calibration_status_insufficient_when_position_bias_is_none() -> None:
    result = compute_calibration_status(
        agreement=0.95, position_bias=None,
        valid_human_pair_count=200, position_pair_count=200,
    )
    assert result.calibration_status == "insufficient"


def test_calibration_status_failing_when_agreement_below_passing() -> None:
    """Per revision #9: anything below 0.70 is failing (no 0.60 floor)."""

    result = compute_calibration_status(
        agreement=0.65, position_bias=0.05,  # below 0.70 → failing
        valid_human_pair_count=200, position_pair_count=200,
    )
    assert result.calibration_status == "failing"
    assert result.usage_mode == "diagnostic_only"


def test_calibration_status_failing_when_position_bias_above_passing() -> None:
    result = compute_calibration_status(
        agreement=0.95, position_bias=0.20,  # above 0.15 → failing
        valid_human_pair_count=200, position_pair_count=200,
    )
    assert result.calibration_status == "failing"


def test_calibration_status_passing_when_thresholds_met() -> None:
    result = compute_calibration_status(
        agreement=0.75,
        position_bias=0.10,
        valid_human_pair_count=120,
        position_pair_count=120,
    )
    assert result.calibration_status == "passing"
    assert result.usage_mode == "gate_eligible"


def test_calibration_status_at_exact_thresholds_is_passing() -> None:
    """Equal thresholds are inclusive (≥ / ≤)."""

    result = compute_calibration_status(
        agreement=THRESHOLDS["agreement_min_passing"],
        position_bias=THRESHOLDS["position_bias_max_passing"],
        valid_human_pair_count=int(THRESHOLDS["valid_human_pair_min"]),
        position_pair_count=int(THRESHOLDS["position_pair_min"]),
    )
    assert result.calibration_status == "passing"
    assert result.usage_mode == "gate_eligible"


def test_calibration_policy_version_constant() -> None:
    assert CALIBRATION_POLICY_VERSION == "v1"


def test_thresholds_constants() -> None:
    assert THRESHOLDS["agreement_min_passing"] == 0.70
    assert THRESHOLDS["position_bias_max_passing"] == 0.15
    assert THRESHOLDS["valid_human_pair_min"] == 100
    assert THRESHOLDS["position_pair_min"] == 100


# --------------------------------------- pair consensus status


def test_pair_status_single_when_one_primary() -> None:
    annotations = [_ann("alice", "P1", "baseline")]
    assert derive_pair_consensus_status(annotations, has_adjudication=False) == "single"


def test_pair_status_consensus_when_two_primaries_agree() -> None:
    annotations = [
        _ann("alice", "P1", "baseline"),
        _ann("bob", "P1", "baseline"),
    ]
    assert derive_pair_consensus_status(annotations, has_adjudication=False) == "consensus"


def test_pair_status_dispute_when_two_primaries_disagree() -> None:
    annotations = [
        _ann("alice", "P1", "baseline"),
        _ann("bob", "P1", "candidate"),
    ]
    status = derive_pair_consensus_status(annotations, has_adjudication=False)
    assert status == "dispute_no_adjudication"


def test_pair_status_adjudicated_after_third_reviewer() -> None:
    annotations = [
        _ann("alice", "P1", "baseline"),
        _ann("bob", "P1", "candidate"),
    ]
    assert derive_pair_consensus_status(annotations, has_adjudication=True) == "adjudicated"
