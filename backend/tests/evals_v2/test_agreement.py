"""Tests for inter-rater agreement metrics (kappa, Spearman, bands, gate)."""

from __future__ import annotations

import pytest

from evals.v2.agreement import (
    band,
    banded_agreement,
    calibration_verdict,
    cohens_kappa,
    spearman_rho,
)


def test_band_threshold() -> None:
    assert band(4) == "good"
    assert band(5) == "good"
    assert band(3) == "bad"
    assert band(1) == "bad"


def test_perfect_agreement_kappa_one() -> None:
    human = [5, 3, 4, 2, 5, 1]
    judge = [5, 3, 4, 2, 5, 1]
    assert banded_agreement(human, judge) == 1.0
    assert cohens_kappa(["a", "b", "a"], ["a", "b", "a"]) == pytest.approx(1.0)


def test_kappa_chance_corrected() -> None:
    # 90% observed agreement on imbalanced labels: kappa (0.615) < raw
    # agreement (0.9) because chance agreement is high (0.74).
    human = ["good"] * 8 + ["bad", "bad"]
    judge = ["good"] * 8 + ["bad", "good"]
    kappa = cohens_kappa(human, judge)
    assert kappa == pytest.approx((0.9 - 0.74) / (1 - 0.74))
    assert kappa < 0.9
    # Observed agreement exactly at chance level -> kappa 0.
    left = ["yes", "no", "yes", "no"]
    right = ["yes", "no", "no", "yes"]
    assert cohens_kappa(left, right) == pytest.approx(0.0)
    # Perfect disagreement on balanced labels -> kappa -1.
    assert cohens_kappa(["yes", "no"], ["no", "yes"]) == pytest.approx(-1.0)


def test_kappa_constant_raters_undefined() -> None:
    assert cohens_kappa(["good"] * 5, ["good"] * 5) == 0.0


def test_spearman_perfect_and_inverse() -> None:
    assert spearman_rho([1, 2, 3, 4, 5], [10, 20, 30, 40, 50]) == pytest.approx(1.0)
    assert spearman_rho([1, 2, 3, 4, 5], [5, 4, 3, 2, 1]) == pytest.approx(-1.0)
    # sum d^2 = 8 over n=5: rho = 1 - 6*8/(5*24) = 0.6
    assert spearman_rho([1, 2, 3, 4, 5], [3, 1, 2, 5, 4]) == pytest.approx(0.6)


def test_spearman_handles_ties_and_constants() -> None:
    assert spearman_rho([1, 2, 2, 3], [1, 2, 2, 3]) == pytest.approx(1.0)
    assert spearman_rho([2, 2, 2], [1, 2, 3]) == 0.0


def test_mismatched_inputs_rejected() -> None:
    with pytest.raises(ValueError):
        banded_agreement([1, 2], [3])
    with pytest.raises(ValueError):
        spearman_rho([], [])
    with pytest.raises(ValueError):
        spearman_rho([1.0], [2.0])


def test_calibration_gate() -> None:
    assert (
        calibration_verdict(kappa=0.7, rho=0.8, agreement=0.85) == "calibrated"
    )
    assert (
        calibration_verdict(kappa=0.7, rho=0.8, agreement=0.75) == "diagnostic_only"
    )
    assert (
        calibration_verdict(kappa=0.55, rho=0.9, agreement=0.9) == "diagnostic_only"
    )
