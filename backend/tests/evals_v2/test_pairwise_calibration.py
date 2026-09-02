"""PR-9c.1 calibration pure-function tests (no PG, no LLM).

Pins (per user-corrected semantics):

* ``agreement_rate`` returns ``MetricResult(value=None, sample_count=0)``
  for empty input; raises on length mismatch; computes flat exact-match.
* ``position_consistency_rate`` measures whether ``normalized_winner`` is
  stable under swap — NOT 50/50 swap distribution. A Judge that always
  picks the side shown as "a" yields consistency 0 even though raw
  distribution is balanced.
* ``position_bias_rate == 1 - position_consistency_rate``.
* Pairs that are tie/both_unacceptable on BOTH orientations carry no
  position signal and are excluded from the denominator; if every pair is
  signal-less, the result is ``value=None``.
"""

from __future__ import annotations

from typing import cast

import pytest

from evals.v2.calibration import (
    MetricResult,
    agreement_rate,
    position_bias_rate,
    position_consistency_rate,
)
from evals.v2.judge import WinLabel

# ------------------------------------------------------- agreement_rate


def test_agreement_empty_returns_none() -> None:
    result = agreement_rate(cast(list[WinLabel], []), cast(list[WinLabel], []))
    assert result == MetricResult(value=None, sample_count=0)


def test_agreement_length_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="length mismatch"):
        agreement_rate(cast(list[WinLabel], ["a"]), cast(list[WinLabel], ["a", "b"]))


def test_agreement_perfect_match() -> None:
    labels: list[WinLabel] = ["a", "b", "tie", "both_unacceptable"]
    result = agreement_rate(labels, labels)
    assert result.value == 1.0
    assert result.sample_count == 4


def test_agreement_partial_match() -> None:
    judge: list[WinLabel] = ["a", "b", "tie", "a"]
    human: list[WinLabel] = ["a", "tie", "tie", "b"]
    result = agreement_rate(judge, human)
    # positions 0 and 2 match → 2/4 = 0.5
    assert result.value == 0.5
    assert result.sample_count == 4


def test_agreement_no_match_returns_zero_not_none() -> None:
    # Distinct from empty input: 0 of N (not 0 of 0) still returns 0.0.
    judge: list[WinLabel] = ["a", "a"]
    human: list[WinLabel] = ["b", "b"]
    result = agreement_rate(judge, human)
    assert result.value == 0.0
    assert result.sample_count == 2


# ----------------------------------------------- position_consistency_rate


def test_position_consistency_empty_returns_none() -> None:
    result = position_consistency_rate(cast(list[WinLabel], []), cast(list[WinLabel], []))
    assert result == MetricResult(value=None, sample_count=0)


def test_position_consistency_length_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="length mismatch"):
        position_consistency_rate(cast(list[WinLabel], ["a"]), cast(list[WinLabel], ["a", "b"]))


def test_position_consistency_perfectly_consistent() -> None:
    # Same normalized winner in both orientations → consistency 1.0.
    regular: list[WinLabel] = ["a", "b", "a"]
    swapped: list[WinLabel] = ["a", "b", "a"]
    result = position_consistency_rate(regular, swapped)
    assert result.value == 1.0
    assert result.sample_count == 3


def test_position_consistency_position_biased_judge_is_zero() -> None:
    """A Judge that always picks "a" (always the first-displayed side):
    after normalization the regular run picks baseline, the swapped run
    picks candidate (since baseline was shown as B). Consistency = 0.

    This is the CRITICAL user correction: balanced 50/50 raw distribution
    is NOT bias=0. This judge has a perfectly balanced raw distribution
    yet consistency 0.

    However, position_consistency_rate takes *already normalized* input:
    the caller is responsible for normalizing. So we feed the normalized
    form directly."""

    regular_normalized: list[WinLabel] = ["a", "a"]
    swapped_normalized: list[WinLabel] = ["b", "b"]
    result = position_consistency_rate(regular_normalized, swapped_normalized)
    assert result.value == 0.0
    assert result.sample_count == 2


def test_position_consistency_excludes_signal_less_pairs() -> None:
    """Pairs that are tie/both_unacceptable on BOTH orientations are
    excluded from the denominator (no position signal)."""

    regular: list[WinLabel] = ["a", "tie", "b"]
    swapped: list[WinLabel] = ["a", "tie", "b"]
    # The middle pair is "tie" on both sides → no signal. The other two
    # are decisive and agree → 2 consistent / 2 with signal = 1.0.
    result = position_consistency_rate(regular, swapped)
    assert result.value == 1.0
    assert result.sample_count == 2


def test_position_consistency_all_signal_less_returns_none() -> None:
    regular: list[WinLabel] = ["tie", "both_unacceptable"]
    swapped: list[WinLabel] = ["tie", "both_unacceptable"]
    result = position_consistency_rate(regular, swapped)
    assert result == MetricResult(value=None, sample_count=0)


def test_position_consistency_partial_disagreement() -> None:
    # 3 decisive pairs; 1 agrees (pair 0: a==a), 2 disagree → 1/3.
    regular: list[WinLabel] = ["a", "b", "a"]
    swapped: list[WinLabel] = ["a", "a", "b"]
    result = position_consistency_rate(regular, swapped)
    assert result.value == pytest.approx(1 / 3)
    assert result.sample_count == 3


# ---------------------------------------------------- position_bias_rate


def test_position_bias_is_one_minus_consistency() -> None:
    regular: list[WinLabel] = ["a", "b"]
    swapped: list[WinLabel] = ["a", "b"]
    bias = position_bias_rate(regular, swapped)
    assert bias.value == 0.0
    assert bias.sample_count == 2


def test_position_bias_maximal_when_fully_inconsistent() -> None:
    regular: list[WinLabel] = ["a", "a"]
    swapped: list[WinLabel] = ["b", "b"]
    bias = position_bias_rate(regular, swapped)
    assert bias.value == 1.0
    assert bias.sample_count == 2


def test_position_bias_returns_none_when_no_signal() -> None:
    regular: list[WinLabel] = ["tie"]
    swapped: list[WinLabel] = ["tie"]
    bias = position_bias_rate(regular, swapped)
    assert bias.value is None
    assert bias.sample_count == 0
