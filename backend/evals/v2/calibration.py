"""PR-9c.1 calibration pure functions: agreement + position-bias metrics.

These are pure (stdlib-only) functions over flat verdict sequences. The
Judge-side normalization lives in ``evals/v2/judge.py``; here we only
compute aggregate metrics from already-normalized verdicts.

This module deliberately contains NO thresholds and NO ``calibration_status``
enum. Per the user's PR-9c.1 decision, threshold gating
(agreement ≥ 0.70 / position_bias ≤ 0.15) belongs to PR-9c.2's calibration
workflow, not the core. PR-9c.1 ships the raw arithmetic.

Semantics pinned by user corrections (see recon report):

* ``position_consistency_rate`` measures whether ``normalized_winner``
  stays stable when the A/B position is swapped — it is NOT "balanced
  50/50 swap distribution". A Judge that always picks "a" produces a
  50/50 raw-distribution but a 0.0 consistency rate.
* ``position_bias_rate`` is ``1 - position_consistency_rate``.
* Empty inputs return ``MetricResult(value=None, sample_count=N)`` rather
  than a misleading 0.0; callers must branch on ``value is None``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from evals.v2.judge import WinLabel

# A human label uses the same vocabulary as the Judge's normalized winner.
# Cohens κ and per-slice confusion matrices live in PR-9c.2; PR-9c.1 only
# needs flat agreement on the four-value vocabulary.
HumanVerdictLabel = WinLabel
JudgeVerdictLabel = WinLabel

VerdictValue = Literal["a", "b", "tie", "both_unacceptable"]


@dataclass(frozen=True, slots=True)
class MetricResult:
    """Aggregate metric value with the sample size it was computed from.

    ``value`` is ``None`` when the metric is undefined for the input
    (empty sequence, or no pair contributes the required signal). The
    sample_count records how many inputs were inspected so callers can
    distinguish "0 of 0" from "0 of N".
    """

    value: float | None
    sample_count: int


def agreement_rate(
    judge_labels: list[JudgeVerdictLabel],
    human_labels: list[HumanVerdictLabel],
) -> MetricResult:
    """Flat exact-match agreement between Judge and human verdicts.

    The two lists must be the same length and ordered identically
    (same Pair, same orientation). The metric is the fraction of positions
    where the Judge's normalized winner equals the human label.

    Returns ``MetricResult(None, 0)`` for empty input. Raises ``ValueError``
    on length mismatch — silent truncation would hide a wiring bug.
    """

    if len(judge_labels) != len(human_labels):
        raise ValueError(
            f"agreement_rate length mismatch: {len(judge_labels)} vs {len(human_labels)}"
        )
    sample_count = len(judge_labels)
    if sample_count == 0:
        return MetricResult(value=None, sample_count=0)
    matches = sum(1 for j, h in zip(judge_labels, human_labels, strict=True) if j == h)
    return MetricResult(value=matches / sample_count, sample_count=sample_count)


def _is_position_decisive(winner: WinLabel) -> bool:
    """A winner is "position-decisive" iff swapping A/B could change it.

    "a"/"b" depend on which side was which; "tie"/"both_unacceptable" are
    position-invariant. Consistency is only meaningful on decisive winners.
    """

    return winner in ("a", "b")


def position_consistency_rate(
    normalized_winners_regular: list[WinLabel],
    normalized_winners_swapped: list[WinLabel],
) -> MetricResult:
    """Fraction of Pairs whose normalized winner is stable under swap.

    For each Pair we (conceptually) ran the Judge twice: once at
    ``PositionVariant.BASELINE`` and once at ``PositionVariant.SWAPPED``.
    After normalization both should agree on the baseline-relative winner.
    This metric is over Pairs where AT LEAST ONE of the two runs produced
    a decisive (a/b) winner — Pairs that are "tie"/"both_unacceptable" on
    both orientations are position-invariant by construction and carry no
    signal about consistency.

    The two input lists must be parallel (same Pair at the same index,
    regular orientation in the first, swapped orientation in the second).

    Returns ``MetricResult(None, N)`` if no Pair is decisive in either
    orientation (no signal). Otherwise the rate is
    ``consistent_pairs / pairs_with_decisive_signal``.
    """

    if len(normalized_winners_regular) != len(normalized_winners_swapped):
        raise ValueError(
            "position_consistency_rate length mismatch: "
            f"{len(normalized_winners_regular)} vs {len(normalized_winners_swapped)}"
        )

    consistent = 0
    with_signal = 0
    for regular, swapped in zip(
        normalized_winners_regular, normalized_winners_swapped, strict=True
    ):
        if not (_is_position_decisive(regular) or _is_position_decisive(swapped)):
            # Both orientations are tie/both_unacceptable — no position
            # bias signal here. Skip rather than count as consistent.
            continue
        with_signal += 1
        if regular == swapped:
            consistent += 1

    if with_signal == 0:
        return MetricResult(value=None, sample_count=0)
    return MetricResult(value=consistent / with_signal, sample_count=with_signal)


def position_bias_rate(
    normalized_winners_regular: list[WinLabel],
    normalized_winners_swapped: list[WinLabel],
) -> MetricResult:
    """``1 - position_consistency_rate`` (per the user's correction).

    A Judge with perfect position consistency has bias 0. A Judge whose
    normalized winner flips whenever the display position is swapped has
    bias 1. This is independent of how often "a" vs "b" occurs overall —
    balanced 50/50 swap assignment is NOT bias=0.
    """

    consistency = position_consistency_rate(
        normalized_winners_regular, normalized_winners_swapped
    )
    if consistency.value is None:
        return MetricResult(value=None, sample_count=consistency.sample_count)
    return MetricResult(value=1.0 - consistency.value, sample_count=consistency.sample_count)
