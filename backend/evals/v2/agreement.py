"""Inter-rater agreement metrics for judge calibration.

Pure functions over paired ratings:

* ``cohens_kappa`` — chance-corrected agreement for categorical ratings
  (used on banded scores: good ≥4 / bad ≤3).
* ``spearman_rho`` — rank correlation for ordinal 1–5 scores.
* ``banded_agreement`` — raw fraction of matching bands.

Design notes:
* Ratings of different scales (e.g. human 1–5 vs judge 1–5) are fine;
  mismatched *lengths* raise ValueError — a calibration report over
  partial data must be explicit about it.
* kappa is computed over the union of observed labels; with no variance
  in either rater (e.g. everything "good") kappa is undefined and
  returned as 0.0 with the caveat left to the caller's report.
"""

from __future__ import annotations

from collections.abc import Sequence
from math import sqrt

GOOD_BAND_THRESHOLD = 4


def _validate(left: Sequence[object], right: Sequence[object]) -> None:
    if len(left) != len(right):
        raise ValueError("raters must have the same number of ratings")
    if not left:
        raise ValueError("ratings must not be empty")


def band(score: int) -> str:
    """Map a 1–5 score to the calibration band (good / bad)."""

    return "good" if score >= GOOD_BAND_THRESHOLD else "bad"


def banded_agreement(
    human: Sequence[int], judge: Sequence[int]
) -> float:
    """Fraction of items whose good/bad bands match."""

    _validate(human, judge)
    return sum(
        1 for left, right in zip(human, judge, strict=True) if band(left) == band(right)
    ) / len(human)


def cohens_kappa(
    human: Sequence[object], judge: Sequence[object]
) -> float:
    """Cohen's kappa over categorical ratings with chance correction."""

    _validate(human, judge)
    total = len(human)
    labels = sorted({*human, *judge}, key=repr)
    observed = sum(
        1 for left, right in zip(human, judge, strict=True) if left == right
    ) / total
    human_counts = {label: 0.0 for label in labels}
    judge_counts = {label: 0.0 for label in labels}
    for left, right in zip(human, judge, strict=True):
        human_counts[left] += 1
        judge_counts[right] += 1
    expected = sum(
        (human_counts[label] / total) * (judge_counts[label] / total)
        for label in labels
    )
    if expected == 1.0:
        # Both raters constant and equal: no variance to correct against.
        return 0.0
    return (observed - expected) / (1 - expected)


def _rank(scores: Sequence[float]) -> list[float]:
    """Average ranks for ties (fractional ranking)."""

    order = sorted(range(len(scores)), key=lambda index: scores[index])
    ranks = [0.0] * len(scores)
    index = 0
    while index < len(order):
        tie_end = index
        while (
            tie_end + 1 < len(order)
            and scores[order[tie_end + 1]] == scores[order[index]]
        ):
            tie_end += 1
        average_rank = (index + 1 + tie_end + 1) / 2
        for position in range(index, tie_end + 1):
            ranks[order[position]] = average_rank
        index = tie_end + 1
    return ranks


def spearman_rho(left: Sequence[float], right: Sequence[float]) -> float:
    """Spearman rank correlation with tie handling; 0.0 when constant."""

    _validate(left, right)
    if len(left) < 2:
        raise ValueError("spearman needs at least two paired ratings")
    left_ranks = _rank(list(left))
    right_ranks = _rank(list(right))
    mean_left = sum(left_ranks) / len(left_ranks)
    mean_right = sum(right_ranks) / len(right_ranks)
    numerator = sum(
        (a - mean_left) * (b - mean_right)
        for a, b in zip(left_ranks, right_ranks, strict=True)
    )
    denominator_left = sqrt(
        sum((a - mean_left) ** 2 for a in left_ranks)
    )
    denominator_right = sqrt(
        sum((b - mean_right) ** 2 for b in right_ranks)
    )
    if denominator_left == 0.0 or denominator_right == 0.0:
        return 0.0
    return numerator / (denominator_left * denominator_right)


KAPPA_GATE = 0.6
SPEARMAN_GATE = 0.75
AGREEMENT_GATE = 0.80


def calibration_verdict(
    *,
    kappa: float,
    rho: float,
    agreement: float,
) -> str:
    """Gate decision: 'calibrated' unlocks judge scores; else diagnostic_only."""

    if kappa >= KAPPA_GATE and rho >= SPEARMAN_GATE and agreement >= AGREEMENT_GATE:
        return "calibrated"
    return "diagnostic_only"
