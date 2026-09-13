"""PR-9a statistical aggregation helpers for multi-trial experiments.

Pure-Python (only stdlib) stats + per-case / per-experiment aggregations.
Zero migrations, zero new deps. Deterministic closed-form intervals.

Public surface::

    wilson_ci(successes, n)              # binomial success-rate 95% CI
    normal_ci(values)                    # normal-approx 95% CI for the mean
    CIInterval / CaseStat / ExperimentStat  # frozen dataclasses
    compute_case_stats(summaries, grade_lookup)
    compute_experiment_stats(case_stats)

Aggregation semantics
---------------------
* Grouping is per ``case_id`` **and** ``variant is None``. Counterfactual
  paired variants (PR-8) are excluded from the experiment-level rollup so
  their contamination / context / tool measurements do not dilute the main
  regression pass-rate; per-variant deltas live in
  ``ExperimentReport.counterfactual_pairs``.
* "Passed" means every EvalScore row marked as a hard gate has
  ``passed=True``. Non-hard-gate rows do not block a Trial. No EvalScore ->
  None (no signal).
* "First attempt" is the Trial with ``trial_index == 0`` in the group.
  ``first_attempt_passed`` is None when that index is missing.
* Runtime failures (``error_code in runtime_failure_codes``) count toward
  the denominator of success rate but never toward the numerator, and are
  surfaced separately as ``runtime_failure_count``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from app.harness.errors import (
    RUNTIME_FAILURE_CATEGORIES,
    EvalFailureCode,
    FailureCategory,
)
from app.harness.errors import (
    USER_CANCEL_CODES as _USER_CANCEL_CODES_TAXONOMY,
)
from app.harness.errors import (
    category as _failure_category,
)

if TYPE_CHECKING:
    # Avoid an import-time cycle: experiment_runner -> stats is fine; the
    # reverse direction (stats -> experiment_runner) is guarded here.
    from evals.v2.experiment_runner import TrialSummary

# 95% two-sided z-infinity (more than enough precision for 30-case datasets).
_Z_95 = 1.959963984540054


def runtime_failure_codes() -> frozenset[str]:
    """Return every canonical code currently classified as a runtime failure.

    Used by tests to assert the bucket is non-empty and by other tests
    to construct stub trials whose ``error_code`` lands in the bucket.
    """

    return frozenset(
        code.value
        for code in EvalFailureCode
        if _failure_category(code.value) in RUNTIME_FAILURE_CATEGORIES
    )


def _is_runtime_failure(code: str | None) -> bool:
    if not code:
        return False
    return _failure_category(code) in RUNTIME_FAILURE_CATEGORIES


def _is_configuration_failure(code: str | None) -> bool:
    if not code:
        return False
    return _failure_category(code) == FailureCategory.CONFIG


def _is_user_cancel(code: str | None) -> bool:
    if not code:
        return False
    return _failure_category(code) == FailureCategory.USER_ACTION


# Back-compat module-level exports (some callers read these constants
# directly). They are computed from the taxonomy above and frozen.
RUNTIME_FAILURE_CODES: tuple[str, ...] = tuple(sorted(runtime_failure_codes()))
USER_CANCEL_CODES: tuple[str, ...] = tuple(sorted(_USER_CANCEL_CODES_TAXONOMY))


# ---------------------------------------------------------------------------
# Confidence intervals
# ---------------------------------------------------------------------------


def wilson_ci(successes: int, n: int, z: float = _Z_95) -> tuple[float, float]:
    """Wilson score 95% CI for a binomial success rate.

    Returns ``(low, high)`` clamped to ``[0, 1]``. ``n <= 0`` -> ``(0.0, 0.0)``.
    Closed-form, deterministic, no resampling required.
    """

    if n <= 0:
        return 0.0, 0.0
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def normal_ci(values: list[float], z: float = _Z_95) -> tuple[float, float]:
    """Normal-approximation 95% confidence interval for the sample mean.

    Uses sample variance (``n - 1``) and standard error (``sd / sqrt(n)``).
    ``n == 0`` returns the existing empty sentinel ``(0.0, 0.0)``. ``n == 1``
    returns the point value for report compatibility; it is a non-inferential
    sentinel because variance cannot be estimated from one observation.
    """

    n = len(values)
    if n == 0:
        return 0.0, 0.0
    mean = sum(values) / n
    if n < 2:
        return mean, mean
    sample_var = sum((v - mean) ** 2 for v in values) / (n - 1)
    standard_error = math.sqrt(sample_var / n)
    return mean - z * standard_error, mean + z * standard_error


def _pop_mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CIInterval:
    low: float
    high: float


@dataclass(frozen=True, slots=True)
class CaseStat:
    """Aggregated stats for one ``(case_id, variant=None)`` group."""

    case_id: str
    trial_count: int
    completed_count: int
    hard_gate_passed_count: int
    runtime_failure_count: int
    # PR-9b: independent buckets so a misconfigured / cancelled Trial does
    # not inflate the runtime-failure or success-rate denominators. Always
    # populated by compute_case_stats (no default needed; ordering invariant).
    configuration_failure_count: int
    cancelled_by_user_count: int
    first_attempt_passed: bool | None
    pass_at_n: bool | None
    pass_all_n: bool | None
    success_rate: float
    success_rate_ci: CIInterval
    mean_tokens_in: float
    mean_tokens_out: float
    mean_latency_ms: float
    tokens_in_ci: CIInterval
    tokens_out_ci: CIInterval
    latency_ci: CIInterval


@dataclass(frozen=True, slots=True)
class ExperimentStat:
    """Experiment-level aggregate computed from CaseStat rows.

    Built by ``compute_experiment_stats`` — never hand-constructed.
    """

    case_count: int
    trial_count: int
    completed_count: int
    hard_gate_passed_count: int
    runtime_failure_count: int
    # PR-9b rollups of the new CaseStat buckets. No defaults —
    # ``compute_experiment_stats`` always populates them.
    configuration_failure_count: int
    cancelled_by_user_count: int
    first_attempt_success_rate: float
    first_attempt_success_rate_ci: CIInterval
    pass_at_n_rate: float
    pass_all_n_rate: float
    success_rate: float
    success_rate_ci: CIInterval


# ---------------------------------------------------------------------------
# Aggregations
# ---------------------------------------------------------------------------

#: ``grade_lookup`` value type: list of
#: (grader_name, score, gate_requirement_passed).
GradeRow = tuple[str, float, bool]


def gate_requirement_passed(*, hard_gate: bool, passed: bool | None) -> bool:
    """Return whether one score row satisfies the Trial's gate requirement.

    A non-gating metric is always neutral. A hard gate passes only when its
    explicit boolean verdict is true; ``None`` therefore fails closed.
    """

    return not hard_gate or passed is True


def _trial_passed(rows: list[GradeRow]) -> bool | None:
    """Three-valued trial-passed verdict.

    * ``True`` when every score row's gate requirement passed (>=1 row).
    * ``False`` when any hard-gate score failed.
    * ``None`` when no scores were persisted (no signal).
    """

    if not rows:
        return None
    return all(gate_passed for _, _, gate_passed in rows)


def quality_trial_count(summaries: list[TrialSummary]) -> int:
    """Count Trials eligible for a quality-success denominator.

    Completed Trials and runtime failures are product outcomes. Configuration
    failures and user cancellations are reported in separate buckets and do
    not become model-quality failures.
    """

    return sum(
        1
        for summary in summaries
        if summary.status == "completed" or _is_runtime_failure(summary.error_code)
    )


def compute_hard_gate_pass_fraction(
    *, passed_count: int, summaries: list[TrialSummary]
) -> float:
    """Compute the report-level pass fraction over quality-eligible Trials."""

    quality_count = quality_trial_count(summaries)
    return round(passed_count / quality_count, 6) if quality_count else 0.0


def compute_case_stats(
    summaries: list[TrialSummary],
    grade_lookup: Mapping[UUID, list[GradeRow]],
) -> dict[str, CaseStat]:
    """Group ``TrialSummary`` rows by ``case_id`` (variant is None only).

    Variant-tagged trials belong to a counterfactual pair and are excluded
    from this aggregation. Their per-variant deltas live in
    ``ExperimentReport.counterfactual_pairs``.

    Failure classification (runtime / configuration / cancel) is sourced
    from ``app.harness.errors``. PR-9a tests that asserted
    ``RUNTIME_FAILURE_CODES`` membership still pass because the tuple is
    re-exported here for back-compat, but the bucket is now derived from
    the canonical ``FailureCategory`` classifier.
    """

    grouped: dict[str, list[TrialSummary]] = {}
    for summary in summaries:
        if summary.variant is not None:
            continue
        grouped.setdefault(summary.case_id, []).append(summary)

    case_stats: dict[str, CaseStat] = {}
    for case_id, group in grouped.items():
        trial_count = len(group)
        # Pass verdicts per trial (None=unknown).
        verdicts: list[bool | None] = []
        verdict_by_trial_id: dict[UUID, bool | None] = {}
        for s in group:
            verdict = _trial_passed(grade_lookup.get(s.trial_id, []))
            if verdict is None and _is_runtime_failure(s.error_code):
                verdict = False
            verdicts.append(verdict)
            verdict_by_trial_id[s.trial_id] = verdict

        hard_gate_passed_count = sum(1 for v in verdicts if v is True)
        completed_count = sum(
            1 for s in group if s.status == "completed"
        )
        runtime_failure_count = sum(
            1
            for s in group
            if s.error_code is not None
            and _is_runtime_failure(s.error_code)
        )
        # PR-9b: classify cancelled Trials. Currently counted in the
        # USER_ACTION bucket which neither inflates runtime_failure nor
        # success_rate. ``cancelled_by_user_count`` is exposed separately.
        cancelled_by_user_count = sum(
            1
            for s in group
            if s.error_code is not None and _is_user_cancel(s.error_code)
        )
        configuration_failure_count = sum(
            1
            for s in group
            if s.error_code is not None
            and _is_configuration_failure(s.error_code)
        )

        first_attempt = next(
            (s for s in group if s.trial_index == 0), None
        )
        if first_attempt is None:
            first_attempt_passed: bool | None = None
        else:
            first_attempt_passed = verdict_by_trial_id[first_attempt.trial_id]

        pass_at_n: bool | None
        pass_all_n: bool | None
        if trial_count == 0:
            pass_at_n = None
            pass_all_n = None
        else:
            pass_at_n = hard_gate_passed_count >= 1
            pass_all_n = hard_gate_passed_count == trial_count

        quality_count = quality_trial_count(group)
        success_rate = (
            hard_gate_passed_count / quality_count if quality_count else 0.0
        )
        low, high = wilson_ci(hard_gate_passed_count, quality_count)

        tokens_in_values = [float(s.tokens_in) for s in group]
        tokens_out_values = [float(s.tokens_out) for s in group]
        latency_values = [float(s.latency_ms) for s in group]

        ti_low, ti_high = normal_ci(tokens_in_values)
        to_low, to_high = normal_ci(tokens_out_values)
        lat_low, lat_high = normal_ci(latency_values)

        case_stats[case_id] = CaseStat(
            case_id=case_id,
            trial_count=trial_count,
            completed_count=completed_count,
            hard_gate_passed_count=hard_gate_passed_count,
            runtime_failure_count=runtime_failure_count,
            configuration_failure_count=configuration_failure_count,
            cancelled_by_user_count=cancelled_by_user_count,
            first_attempt_passed=first_attempt_passed,
            pass_at_n=pass_at_n,
            pass_all_n=pass_all_n,
            success_rate=success_rate,
            success_rate_ci=CIInterval(low=low, high=high),
            mean_tokens_in=_pop_mean(tokens_in_values),
            mean_tokens_out=_pop_mean(tokens_out_values),
            mean_latency_ms=_pop_mean(latency_values),
            tokens_in_ci=CIInterval(low=ti_low, high=ti_high),
            tokens_out_ci=CIInterval(low=to_low, high=to_high),
            latency_ci=CIInterval(low=lat_low, high=lat_high),
        )

    return case_stats


def compute_experiment_stats(
    case_stats: Mapping[str, CaseStat],
) -> ExperimentStat:
    """Roll per-case stats up to experiment-level totals + 95% CIs."""

    rows = list(case_stats.values())
    case_count = len(rows)
    if case_count == 0:
        return ExperimentStat(
            case_count=0,
            trial_count=0,
            completed_count=0,
            hard_gate_passed_count=0,
            runtime_failure_count=0,
            configuration_failure_count=0,
            cancelled_by_user_count=0,
            first_attempt_success_rate=0.0,
            first_attempt_success_rate_ci=CIInterval(low=0.0, high=0.0),
            pass_at_n_rate=0.0,
            pass_all_n_rate=0.0,
            success_rate=0.0,
            success_rate_ci=CIInterval(low=0.0, high=0.0),
        )

    trial_count = sum(c.trial_count for c in rows)
    completed_count = sum(c.completed_count for c in rows)
    hard_gate_passed_count = sum(c.hard_gate_passed_count for c in rows)
    runtime_failure_count = sum(c.runtime_failure_count for c in rows)
    # PR-9b: roll the new independent buckets up to the experiment level.
    configuration_failure_count = sum(
        c.configuration_failure_count for c in rows
    )
    cancelled_by_user_count = sum(c.cancelled_by_user_count for c in rows)

    first_attempts = [c for c in rows if c.first_attempt_passed is not None]
    first_attempt_successes = sum(
        1 for c in first_attempts if c.first_attempt_passed is True
    )
    first_attempt_total = len(first_attempts)
    first_attempt_rate = (
        first_attempt_successes / first_attempt_total
        if first_attempt_total > 0
        else 0.0
    )
    fa_low, fa_high = wilson_ci(first_attempt_successes, first_attempt_total)

    pass_at_n_cases = sum(1 for c in rows if c.pass_at_n is True)
    pass_all_n_cases = sum(1 for c in rows if c.pass_all_n is True)

    quality_count = completed_count + runtime_failure_count
    success_rate = (
        hard_gate_passed_count / quality_count if quality_count else 0.0
    )
    sr_low, sr_high = wilson_ci(hard_gate_passed_count, quality_count)

    return ExperimentStat(
        case_count=case_count,
        trial_count=trial_count,
        completed_count=completed_count,
        hard_gate_passed_count=hard_gate_passed_count,
        runtime_failure_count=runtime_failure_count,
        configuration_failure_count=configuration_failure_count,
        cancelled_by_user_count=cancelled_by_user_count,
        first_attempt_success_rate=first_attempt_rate,
        first_attempt_success_rate_ci=CIInterval(low=fa_low, high=fa_high),
        pass_at_n_rate=pass_at_n_cases / case_count,
        pass_all_n_rate=pass_all_n_cases / case_count,
        success_rate=success_rate,
        success_rate_ci=CIInterval(low=sr_low, high=sr_high),
    )
