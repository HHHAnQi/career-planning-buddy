"""PR-9c.2 calibration metric pure functions.

Public surface:

* :func:`unify_winner_to_baseline_candidate` — convert Judge-side
  ``a/b/tie/both_unacceptable`` to the reviewer-side
  ``baseline/candidate/tie/both_unacceptable`` vocabulary so judge-vs-human
  comparison is on a common axis.
* :func:`flat_agreement_rate` — exact-match agreement.
* :func:`cohens_kappa` — chance-corrected κ. Returns
  ``MetricResult(value=None, sample_count=N)`` when ``1 - p_e == 0`` (per
  user decision #4: a single category with zero marginal probability is
  NOT a degenerate case by itself — only when chance agreement is total).
* :func:`per_dimension_flat_agreement` /
  :func:`per_dimension_cohens_kappa` — five-dimension variants.
* :func:`inter_rater_flat_agreement` /
  :func:`inter_rater_cohens_kappa` — inter-reviewer metrics computed on
  the set of pairs annotated by BOTH reviewers of a reviewer-pair, NOT
  on the disagreement subset (per user revision #6).
* :func:`compute_calibration_status` — three-state status + usage_mode.

Architecture invariant: this module never imports ``app.agent.*`` /
``app.harness.*``. Threshold constants are module-level (NOT Settings)
per supplementary decision #5; bumping any threshold requires bumping
``CALIBRATION_POLICY_VERSION`` and re-running calibration report
generation.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from evals.v2.judge import WinLabel

# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------

# Reviewer-side verdicts use baseline/candidate vocabulary. Judge-side uses
# a/b vocabulary (because the Judge's display is positional, not role-aware).
BaselineCandidateLabel = Literal[
    "baseline", "candidate", "tie", "both_unacceptable"
]
DimensionVerdict = Literal["a", "b", "tie", "both_unacceptable"]
ReviewerDimensionVerdict = Literal[
    "baseline", "candidate", "tie", "both_unacceptable"
]

CALIBRATION_POLICY_VERSION = "v1"

THRESHOLDS = {
    "agreement_min_passing": 0.70,
    "position_bias_max_passing": 0.15,
    "valid_human_pair_min": 100,
    "position_pair_min": 100,
}

DIMENSION_NAMES: tuple[str, ...] = (
    "actionability",
    "alignment",
    "personalization",
    "clarity",
    "consistency",
)


# ---------------------------------------------------------------------------
# Cross-vocabulary unification
# ---------------------------------------------------------------------------


def unify_winner_to_baseline_candidate(winner: WinLabel) -> BaselineCandidateLabel:
    """Map Judge ``WinLabel`` to reviewer ``BaselineCandidateLabel``.

    The Judge emits ``a`` / ``b`` referring to display positions. After
    PR-9c.1 normalization, ``a`` ALWAYS equals ``baseline`` (the Judge
    result's ``normalized_winner`` is already un-swapped). So the unified
    vocabulary has:

        a / baseline    → baseline
        b / candidate   → candidate
        tie             → tie
        both_unacceptable → both_unacceptable

    Kept as an explicit function (not a dict literal) so the contract is
    greppable and reviewable.
    """

    if winner == "a":
        return "baseline"
    if winner == "b":
        return "candidate"
    return winner


def unify_dimensions(
    dims: dict[str, DimensionVerdict],
) -> dict[str, ReviewerDimensionVerdict]:
    """Apply :func:`unify_winner_to_baseline_candidate` per dimension."""

    return {name: unify_winner_to_baseline_candidate(v) for name, v in dims.items()}


# ---------------------------------------------------------------------------
# Metric result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MetricResult:
    """Aggregate metric value with the sample size it was computed from.

    ``value`` is ``None`` when the metric is undefined for the input
    (empty sequence, ``1 - p_e == 0`` for κ, etc.). The ``sample_count``
    records how many inputs were inspected so callers can distinguish
    "0 of 0" from "0 of N".
    """

    value: float | None
    sample_count: int


# ---------------------------------------------------------------------------
# Exact-match agreement
# ---------------------------------------------------------------------------


def flat_agreement_rate(
    judge_labels: list[BaselineCandidateLabel],
    human_labels: list[BaselineCandidateLabel],
) -> MetricResult:
    """Exact-match agreement between two aligned label sequences.

    Raises :class:`ValueError` on length mismatch — silent truncation would
    hide a wiring bug.
    """

    if len(judge_labels) != len(human_labels):
        raise ValueError(
            f"flat_agreement_rate length mismatch: {len(judge_labels)} vs {len(human_labels)}"
        )
    n = len(judge_labels)
    if n == 0:
        return MetricResult(value=None, sample_count=0)
    matches = sum(1 for j, h in zip(judge_labels, human_labels, strict=True) if j == h)
    return MetricResult(value=matches / n, sample_count=n)


def per_dimension_flat_agreement(
    judge_dims: list[dict[str, ReviewerDimensionVerdict]],
    human_dims: list[dict[str, ReviewerDimensionVerdict]],
) -> dict[str, MetricResult]:
    """Per-dimension exact agreement, returned as a dict keyed by dimension."""

    if len(judge_dims) != len(human_dims):
        raise ValueError(
            "per_dimension_flat_agreement length mismatch: "
            f"{len(judge_dims)} vs {len(human_dims)}"
        )
    result: dict[str, MetricResult] = {}
    for dim in DIMENSION_NAMES:
        judge_slice = [d[dim] for d in judge_dims if dim in d]
        human_slice = [d[dim] for d in human_dims if dim in d]
        if len(judge_slice) != len(human_slice) or not judge_slice:
            result[dim] = MetricResult(value=None, sample_count=len(judge_slice))
            continue
        result[dim] = flat_agreement_rate(judge_slice, human_slice)
    return result


# ---------------------------------------------------------------------------
# Cohen's kappa
# ---------------------------------------------------------------------------

_KAPPA_VOCABULARY: tuple[BaselineCandidateLabel, ...] = (
    "baseline",
    "candidate",
    "tie",
    "both_unacceptable",
)


def cohens_kappa(
    judge_labels: list[BaselineCandidateLabel],
    human_labels: list[BaselineCandidateLabel],
) -> MetricResult:
    """Unweighted Cohen's κ on the four-value vocabulary.

    Returns ``MetricResult(None, N)`` when ``1 - p_e == 0`` (e.g. both
    labelers put 100% on the same single category — κ's denominator
    collapses to zero). A category with marginal probability zero on one
    side but not both still has ``p_e < 1`` and is computed normally.
    """

    if len(judge_labels) != len(human_labels):
        raise ValueError(
            f"cohens_kappa length mismatch: {len(judge_labels)} vs {len(human_labels)}"
        )
    n = len(judge_labels)
    if n == 0:
        return MetricResult(value=None, sample_count=0)

    judge_counts = _count_labels(judge_labels)
    human_counts = _count_labels(human_labels)
    p_e = sum((judge_counts[c] / n) * (human_counts[c] / n) for c in _KAPPA_VOCABULARY)
    p_o = sum(
        1 for j, h in zip(judge_labels, human_labels, strict=True) if j == h
    ) / n

    denominator = 1.0 - p_e
    if denominator == 0:
        return MetricResult(value=None, sample_count=n)
    kappa = (p_o - p_e) / denominator
    return MetricResult(value=kappa, sample_count=n)


def per_dimension_cohens_kappa(
    judge_dims: list[dict[str, ReviewerDimensionVerdict]],
    human_dims: list[dict[str, ReviewerDimensionVerdict]],
) -> dict[str, MetricResult]:
    """Per-dimension Cohen's κ."""

    if len(judge_dims) != len(human_dims):
        raise ValueError(
            "per_dimension_cohens_kappa length mismatch: "
            f"{len(judge_dims)} vs {len(human_dims)}"
        )
    result: dict[str, MetricResult] = {}
    for dim in DIMENSION_NAMES:
        judge_slice = [d[dim] for d in judge_dims if dim in d]
        human_slice = [d[dim] for d in human_dims if dim in d]
        if len(judge_slice) != len(human_slice) or not judge_slice:
            result[dim] = MetricResult(value=None, sample_count=len(judge_slice))
            continue
        result[dim] = cohens_kappa(judge_slice, human_slice)
    return result


def _count_labels(labels: list[BaselineCandidateLabel]) -> dict[str, int]:
    counts: dict[str, int] = {c: 0 for c in _KAPPA_VOCABULARY}
    for label in labels:
        counts[label] += 1
    return counts


# ---------------------------------------------------------------------------
# Inter-rater metrics (per reviewer-pair, on common items only)
# ---------------------------------------------------------------------------

ReviewerPairId = tuple[str, str]


@dataclass(frozen=True, slots=True)
class ReviewerAnnotation:
    """Reviewer-side annotation view for inter-rater computation.

    ``reviewer_id`` is the JWT subject. ``pair_id`` is the stable Pair row
    identifier. ``label`` is the reviewer's normalized verdict in
    baseline/candidate vocabulary. ``is_adjudication`` flags third-person
    adjudicator annotations; those are EXCLUDED from inter-rater κ.
    """

    reviewer_id: str
    pair_id: str
    label: BaselineCandidateLabel
    is_adjudication: bool = False


def _reviewer_pair_key(a: str, b: str) -> ReviewerPairId:
    """Lexicographic key for an unordered reviewer pair."""

    return (a, b) if a <= b else (b, a)


def inter_rater_flat_agreement(
    annotations: list[ReviewerAnnotation],
) -> MetricResult:
    """Global primary-primary exact agreement on common-pair set.

    Drops adjudication rows. Drops pairs annotated by only one reviewer.
    On the rest, an agreement is when both reviewers picked the same label.
    """

    primaries = [a for a in annotations if not a.is_adjudication]
    by_pair = _group_primaries_by_pair(primaries)
    common = [
        pair_annotations
        for pair_annotations in by_pair.values()
        if len(pair_annotations) == 2
    ]
    if not common:
        return MetricResult(value=None, sample_count=0)
    matches = sum(1 for rp in common if rp[0].label == rp[1].label)
    return MetricResult(value=matches / len(common), sample_count=len(common))


def inter_rater_cohens_kappa(
    annotations: list[ReviewerAnnotation],
) -> dict[ReviewerPairId, MetricResult]:
    """Per-reviewer-pair Cohen's κ on the set of pairs annotated by both.

    For each reviewer-pair ``(X, Y)`` we collect the pairs both have
    annotated, and compute κ on those (per user revision #6 — on the
    common set, NOT on the disagreement subset).
    """

    primaries = [a for a in annotations if not a.is_adjudication]
    by_pair = _group_primaries_by_pair(primaries)

    pair_to_reviewers: dict[str, set[str]] = defaultdict(set)
    pair_label_by_reviewer: dict[tuple[str, str], BaselineCandidateLabel] = {}
    for pair_id, anns in by_pair.items():
        for ann in anns:
            pair_to_reviewers[pair_id].add(ann.reviewer_id)
            pair_label_by_reviewer[(pair_id, ann.reviewer_id)] = ann.label

    reviewer_pair_to_pairs: dict[ReviewerPairId, list[str]] = defaultdict(list)
    for pair_id, reviewers in pair_to_reviewers.items():
        # Each pair contributes to AT MOST one reviewer-pair (when exactly
        # 2 primaries reviewed it). With >2 primary reviewers (currently
        # forbidden by PRE-2 invariant, but defensive), it would attribute
        # to every pair.
        if len(reviewers) != 2:
            continue
        reviewer_list = sorted(reviewers)
        reviewer_pair_id = _reviewer_pair_key(reviewer_list[0], reviewer_list[1])
        reviewer_pair_to_pairs[reviewer_pair_id].append(pair_id)

    result: dict[ReviewerPairId, MetricResult] = {}
    for reviewer_pair_id, pair_ids in reviewer_pair_to_pairs.items():
        judge_labels: list[BaselineCandidateLabel] = []
        human_labels: list[BaselineCandidateLabel] = []
        # Stable order across the pair set
        for pair_id in sorted(pair_ids):
            r1, r2 = reviewer_pair_id
            judge_labels.append(pair_label_by_reviewer[(pair_id, r1)])
            human_labels.append(pair_label_by_reviewer[(pair_id, r2)])
        # Within-pair order matters for κ's confusion matrix. Both
        # sequences are in the same order, so a swap of reviewer labels
        # produces the same κ (κ is symmetric across raters on common
        # items).
        result[reviewer_pair_id] = cohens_kappa(judge_labels, human_labels)
    return result


def _group_primaries_by_pair(
    primaries: list[ReviewerAnnotation],
) -> dict[str, list[ReviewerAnnotation]]:
    grouped: dict[str, list[ReviewerAnnotation]] = defaultdict(list)
    for ann in primaries:
        grouped[ann.pair_id].append(ann)
    return grouped


# ---------------------------------------------------------------------------
# Calibration status
# ---------------------------------------------------------------------------

CalibrationStatus = Literal["passing", "failing", "insufficient"]
UsageMode = Literal["diagnostic_only", "gate_eligible"]
ReviewerConsensusStatus = Literal[
    "single", "consensus", "dispute_no_adjudication", "adjudicated"
]


@dataclass(frozen=True, slots=True)
class CalibrationOutcome:
    """Combined status + usage mode for a calibration report."""

    calibration_status: CalibrationStatus
    usage_mode: UsageMode


def compute_calibration_status(
    *,
    agreement: float | None,
    position_bias: float | None,
    valid_human_pair_count: int,
    position_pair_count: int,
) -> CalibrationOutcome:
    """Three-state calibration status with derived usage_mode.

    Decision tree (per user revision #9 — eliminates the 0.60-0.70 grey
    band: anything below ``agreement_min_passing`` or above
    ``position_bias_max_passing`` is failing, NOT a third "borderline"
    tier):

    1. ``valid_human_pair_count < valid_human_pair_min``                     → ``insufficient``
    2. ``position_pair_count < position_pair_min``                           → ``insufficient``
    3. ``agreement is None`` (kappa degenerate)                              → ``insufficient``
    4. ``position_bias is None`` (no decisive pairs)                         → ``insufficient``
    5. ``agreement < agreement_min_passing``                                 → ``failing``
    6. ``position_bias > position_bias_max_passing``                         → ``failing``
    7. else                                                                  → ``passing``

    ``usage_mode``:

    * ``diagnostic_only`` — when ``calibration_status`` is ``insufficient``
      or ``failing``. The Judge is uncalibrated and its verdicts MUST NOT
      gate anything. (PR-9c.2 ships no consumer of ``gate_eligible`` —
      that's future work; this flag is purely advisory in this PR.)
    * ``gate_eligible`` — only when ``calibration_status == 'passing'``.
    """

    if (
        valid_human_pair_count < THRESHOLDS["valid_human_pair_min"]
        or position_pair_count < THRESHOLDS["position_pair_min"]
        or agreement is None
        or position_bias is None
    ):
        return CalibrationOutcome("insufficient", "diagnostic_only")
    if agreement < THRESHOLDS["agreement_min_passing"]:
        return CalibrationOutcome("failing", "diagnostic_only")
    if position_bias > THRESHOLDS["position_bias_max_passing"]:
        return CalibrationOutcome("failing", "diagnostic_only")
    return CalibrationOutcome("passing", "gate_eligible")


def derive_pair_consensus_status(
    primaries: list[ReviewerAnnotation],
    *,
    has_adjudication: bool,
) -> ReviewerConsensusStatus:
    """Map primary annotations + adjudication presence to a pair status.

    * ``single``                       — fewer than 2 primaries.
    * ``consensus``                    — exactly 2 primaries with identical labels.
    * ``dispute_no_adjudication``      — 2 primaries with disagreeing labels and
      no adjudication row.
    * ``adjudicated``                  — any disagreement, plus an adjudication
      row.
    """

    if len(primaries) < 2:
        return "single"
    distinct = {a.label for a in primaries}
    if len(distinct) > 1:
        return "adjudicated" if has_adjudication else "dispute_no_adjudication"
    return "consensus"


__all__ = [
    "CALIBRATION_POLICY_VERSION",
    "CalibrationOutcome",
    "CalibrationStatus",
    "DIMENSION_NAMES",
    "MetricResult",
    "ReviewerAnnotation",
    "ReviewerConsensusStatus",
    "ReviewerDimensionVerdict",
    "ReviewerPairId",
    "THRESHOLDS",
    "BaselineCandidateLabel",
    "cohens_kappa",
    "compute_calibration_status",
    "derive_pair_consensus_status",
    "flat_agreement_rate",
    "inter_rater_cohens_kappa",
    "inter_rater_flat_agreement",
    "per_dimension_cohens_kappa",
    "per_dimension_flat_agreement",
    "unify_dimensions",
    "unify_winner_to_baseline_candidate",
]
