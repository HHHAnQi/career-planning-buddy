"""PR-9a statistical aggregation tests (pure Python, no PostgreSQL).

Exercises the public surface of ``evals/v2/stats.py`` plus the
``ExperimentReport.to_dict()`` integration. All assertions are
deterministic; no async, no DB.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from evals.v2.experiment_runner import (
    ExperimentReport,
    TrialSummary,
)
from evals.v2.stats import (
    CaseStat,
    CIInterval,
    ExperimentStat,
    compute_case_stats,
    compute_experiment_stats,
    compute_hard_gate_pass_fraction,
    gate_requirement_passed,
    normal_ci,
    wilson_ci,
)

# ---------------------------------------------------------------------------
# CI primitives
# ---------------------------------------------------------------------------


def test_wilson_ci_zero_n_returns_zero_interval() -> None:
    assert wilson_ci(0, 0) == (0.0, 0.0)


def test_wilson_ci_all_successes_high_near_one() -> None:
    low, high = wilson_ci(10, 10)
    assert 0.7 < low < 1.0
    assert abs(high - 1.0) < 1e-9  # clamps to 1.0 within float precision


def test_wilson_ci_mixed_successes_widens_interval() -> None:
    # 3/5: midpoint 0.6, must straddle and stay within [0,1].
    low, high = wilson_ci(3, 5)
    assert 0.0 <= low < 0.6 < high <= 1.0
    # CI for n=5 is wider than n=10000 at the same rate.
    low_big, high_big = wilson_ci(6000, 10000)
    assert (high_big - low_big) < (high - low)


def test_normal_ci_n_lt2_uses_non_inferential_point_sentinel() -> None:
    assert normal_ci([]) == (0.0, 0.0)
    assert normal_ci([42.0]) == (42.0, 42.0)


def test_normal_ci_two_values_symmetric() -> None:
    low, high = normal_ci([10.0, 20.0])
    mean = (low + high) / 2
    assert mean == 15.0
    assert low < 15.0 < high


def test_normal_ci_uses_standard_error_not_population_spread() -> None:
    values = [10.0, 20.0, 30.0, 40.0]
    low, high = normal_ci(values)
    mean = 25.0
    sample_variance = 500.0 / 3.0
    expected_half_width = 1.959963984540054 * (sample_variance / 4) ** 0.5

    assert low == pytest.approx(mean - expected_half_width)
    assert high == pytest.approx(mean + expected_half_width)


def test_normal_ci_shrinks_when_same_distribution_is_repeated() -> None:
    small = [10.0, 20.0, 30.0, 40.0]
    large = small * 10
    small_low, small_high = normal_ci(small)
    large_low, large_high = normal_ci(large)

    assert (large_high - large_low) < (small_high - small_low)


# ---------------------------------------------------------------------------
# TrialSummary fixture (straw-man; full report builder doesn't matter here)
# ---------------------------------------------------------------------------


def _summary(
    *,
    case_id: str = "c1",
    variant: str | None = None,
    trial_index: int = 0,
    status: str = "completed",
    error_code: str | None = None,
) -> TrialSummary:
    return TrialSummary(
        trial_id=uuid4(),
        case_id=case_id,
        status=status,
        run_status="completed",
        result_kind="plan",
        tokens_in=100,
        tokens_out=200,
        latency_ms=500,
        error_code=error_code,
        terminal_event_count=1,
        tool_call_count=0,
        variant=variant,
        trial_index=trial_index,
    )


def _grades_flag(passed: bool) -> list[tuple[str, float, bool]]:
    """One grade row carrying the requested gate verdict."""

    return [("model.token_usage_nonzero", 1.0, passed)]


def test_gate_requirement_passed_uses_verdict_only_for_hard_gates() -> None:
    assert gate_requirement_passed(hard_gate=True, passed=True) is True
    assert gate_requirement_passed(hard_gate=True, passed=False) is False
    assert gate_requirement_passed(hard_gate=True, passed=None) is False
    assert gate_requirement_passed(hard_gate=False, passed=False) is True
    assert gate_requirement_passed(hard_gate=False, passed=None) is True


def test_compute_case_stats_non_gate_failure_does_not_block_trial() -> None:
    summary = _summary()
    grade_lookup = {
        summary.trial_id: [
            (
                "task.required_constraint",
                1.0,
                gate_requirement_passed(hard_gate=True, passed=True),
            ),
            (
                "model.advisory_quality",
                0.0,
                gate_requirement_passed(hard_gate=False, passed=False),
            ),
        ]
    }

    stat = compute_case_stats([summary], grade_lookup)["c1"]

    assert stat.first_attempt_passed is True
    assert stat.hard_gate_passed_count == 1
    assert stat.success_rate == 1.0


def test_compute_case_stats_failed_hard_gate_blocks_trial() -> None:
    summary = _summary()
    grade_lookup = {
        summary.trial_id: [
            (
                "task.required_constraint",
                0.0,
                gate_requirement_passed(hard_gate=True, passed=False),
            ),
            (
                "model.advisory_quality",
                1.0,
                gate_requirement_passed(hard_gate=False, passed=True),
            ),
        ]
    }

    stat = compute_case_stats([summary], grade_lookup)["c1"]

    assert stat.first_attempt_passed is False
    assert stat.hard_gate_passed_count == 0
    assert stat.success_rate == 0.0


# ---------------------------------------------------------------------------
# compute_case_stats
# ---------------------------------------------------------------------------


def test_compute_case_stats_first_attempt_passed_when_index_0_passes() -> None:
    s0 = _summary(trial_index=0)  # passing trial
    s1 = _summary(trial_index=1)  # failing trial
    s2 = _summary(trial_index=2)  # failing trial
    grade_lookup = {
        s0.trial_id: _grades_flag(True),
        s1.trial_id: _grades_flag(False),
        s2.trial_id: _grades_flag(False),
    }
    stats = compute_case_stats([s0, s1, s2], grade_lookup)
    assert "c1" in stats
    stat = stats["c1"]
    assert stat.trial_count == 3
    assert stat.first_attempt_passed is True  # trial_index=0
    assert stat.hard_gate_passed_count == 1
    assert stat.pass_at_n is True  # >=1 of 3
    assert stat.pass_all_n is False  # not all 3


def test_compute_case_stats_first_attempt_failed_when_index_0_fails() -> None:
    s0 = _summary(trial_index=0)
    s1 = _summary(trial_index=1)
    grade_lookup = {
        s0.trial_id: _grades_flag(False),
        s1.trial_id: _grades_flag(True),
    }
    stats = compute_case_stats([s0, s1], grade_lookup)
    stat = stats["c1"]
    assert stat.first_attempt_passed is False
    assert stat.hard_gate_passed_count == 1
    assert stat.pass_at_n is True


def test_compute_case_stats_pass_all_n_true_when_all_passed() -> None:
    s0 = _summary(trial_index=0)
    s1 = _summary(trial_index=1)
    grade_lookup = {
        s0.trial_id: _grades_flag(True),
        s1.trial_id: _grades_flag(True),
    }
    stats = compute_case_stats([s0, s1], grade_lookup)
    stat = stats["c1"]
    assert stat.pass_all_n is True
    assert stat.pass_at_n is True


def test_compute_case_stats_runtime_failure_count_excludes_user_cancelled() -> None:
    # cancelled trial with USER_REQUESTED_CANCEL (harmed as a runtime failure
    # if a quality verdict), and one with RUN_NOT_COMPLETED (runtime failure).
    s_cancel = _summary(
        trial_index=0, status="cancelled", error_code="USER_REQUESTED_CANCEL"
    )
    s_runtime = _summary(
        trial_index=1, status="failed", error_code="RUN_NOT_COMPLETED"
    )
    stats = compute_case_stats([s_cancel, s_runtime], {})
    stat = stats["c1"]
    assert stat.runtime_failure_count == 1
    assert stat.trial_count == 2
    assert stat.completed_count == 0  # neither completed
    assert stat.hard_gate_passed_count == 0
    assert stat.first_attempt_passed is None  # user cancellation stays neutral
    assert stat.success_rate == 0.0  # one runtime failure in the denominator


def test_runtime_failure_reduces_case_and_experiment_success_rates() -> None:
    passed = _summary(case_id="c1", trial_index=0)
    runtime_failed = _summary(
        case_id="c1",
        trial_index=1,
        status="failed",
        error_code="PROVIDER_TIMEOUT",
    )
    case_stats = compute_case_stats(
        [passed, runtime_failed],
        {passed.trial_id: _grades_flag(True)},
    )

    stat = case_stats["c1"]
    assert stat.completed_count == 1
    assert stat.runtime_failure_count == 1
    assert stat.hard_gate_passed_count == 1
    assert stat.success_rate == 0.5
    assert stat.pass_at_n is True
    assert stat.pass_all_n is False

    experiment = compute_experiment_stats(case_stats)
    assert experiment.success_rate == 0.5


def test_runtime_failure_on_first_attempt_counts_as_failed_attempt() -> None:
    runtime_failed = _summary(
        case_id="c1",
        trial_index=0,
        status="failed",
        error_code="PROVIDER_RATE_LIMITED",
    )

    stat = compute_case_stats([runtime_failed], {})["c1"]

    assert stat.first_attempt_passed is False


def test_report_hard_gate_fraction_includes_runtime_failures() -> None:
    passed = _summary(case_id="passed")
    runtime_failed = _summary(
        case_id="runtime-failed",
        status="failed",
        error_code="PROVIDER_TIMEOUT",
    )

    fraction = compute_hard_gate_pass_fraction(
        passed_count=1,
        summaries=[passed, runtime_failed],
    )

    assert fraction == 0.5


def test_compute_case_stats_drops_non_null_variants() -> None:
    """Variant-tagged trials must NOT appear in case_stats."""

    baseline = _summary(case_id="c1", variant=None, trial_index=0)
    variant_a = _summary(case_id="c1", variant="hidden_evidence", trial_index=0)
    variant_b = _summary(case_id="c1", variant="visible_evidence", trial_index=0)
    grade_lookup = {
        baseline.trial_id: _grades_flag(True),
        variant_a.trial_id: _grades_flag(True),
        variant_b.trial_id: _grades_flag(False),
    }
    stats = compute_case_stats([baseline, variant_a, variant_b], grade_lookup)
    stats_c1 = stats.get("c1")
    assert stats_c1 is not None
    assert stats_c1.trial_count == 1  # baseline only
    assert stats_c1.hard_gate_passed_count == 1


# ---------------------------------------------------------------------------
# compute_experiment_stats
# ---------------------------------------------------------------------------


def test_compute_experiment_stats_aggregates_first_attempt_rate() -> None:
    s_a0 = _summary(case_id="A", trial_index=0)
    s_b0 = _summary(case_id="B", trial_index=0)
    s_b1 = _summary(case_id="B", trial_index=1)
    grade_lookup = {
        s_a0.trial_id: _grades_flag(True),
        s_b0.trial_id: _grades_flag(False),
        s_b1.trial_id: _grades_flag(True),
    }
    case_stats = compute_case_stats([s_a0, s_b0, s_b1], grade_lookup)
    exp = compute_experiment_stats(case_stats)
    assert exp.case_count == 2
    # case A first-attempt: True; case B first-attempt: False.
    assert exp.first_attempt_success_rate == 0.5
    # both cases had >=1 pass, only A had all passes.
    assert exp.pass_at_n_rate == 1.0
    assert exp.pass_all_n_rate == 0.5
    assert exp.trial_count == 3
    assert exp.completed_count == 3
    assert exp.hard_gate_passed_count == 2


def test_compute_experiment_stats_empty_returns_zeroes() -> None:
    exp = compute_experiment_stats({})
    assert exp.case_count == 0
    assert exp.first_attempt_success_rate == 0.0
    assert exp.success_rate == 0.0


# ---------------------------------------------------------------------------
# ExperimentReport.to_dict integration
# ---------------------------------------------------------------------------


def test_experiment_report_to_dict_emits_case_stats_and_experiment_stats() -> None:
    case_stat = CaseStat(
        case_id="c1",
        trial_count=1,
        completed_count=1,
        hard_gate_passed_count=1,
        runtime_failure_count=0,
        configuration_failure_count=0,
        cancelled_by_user_count=0,
        first_attempt_passed=True,
        pass_at_n=True,
        pass_all_n=True,
        success_rate=1.0,
        success_rate_ci=CIInterval(low=0.2, high=1.0),
        mean_tokens_in=100.0,
        mean_tokens_out=200.0,
        mean_latency_ms=300.0,
        tokens_in_ci=CIInterval(low=100.0, high=100.0),
        tokens_out_ci=CIInterval(low=200.0, high=200.0),
        latency_ci=CIInterval(low=300.0, high=300.0),
    )
    exp_stat = ExperimentStat(
        case_count=1,
        trial_count=1,
        completed_count=1,
        hard_gate_passed_count=1,
        runtime_failure_count=0,
        configuration_failure_count=0,
        cancelled_by_user_count=0,
        first_attempt_success_rate=1.0,
        first_attempt_success_rate_ci=CIInterval(low=0.2, high=1.0),
        pass_at_n_rate=1.0,
        pass_all_n_rate=1.0,
        success_rate=1.0,
        success_rate_ci=CIInterval(low=0.2, high=1.0),
    )
    summary = _summary()
    report = ExperimentReport(
        experiment_id=uuid4(),
        experiment_status="completed",
        trial_count=1,
        trials=[summary],
        scored_trial_count=1,
        hard_gate_pass_fraction=1.0,
        case_stats={"c1": case_stat},
        experiment_stats=exp_stat,
    )
    payload = report.to_dict()
    assert "case_stats" in payload
    assert "experiment_stats" in payload
    case_block = payload["case_stats"]
    assert isinstance(case_block, dict)
    assert "c1" in case_block
    exp_block = payload["experiment_stats"]
    assert isinstance(exp_block, dict)
    assert exp_block["first_attempt_success_rate"] == 1.0
    assert exp_block["case_count"] == 1


def test_experiment_report_to_dict_defaults_case_stats_empty_for_backcompat() -> None:
    """A pre-PR-9a-style report still serialises cleanly."""
    summary = _summary()
    report = ExperimentReport(
        experiment_id=uuid4(),
        experiment_status="completed",
        trial_count=1,
        trials=[summary],
    )
    payload = report.to_dict()
    assert payload["case_stats"] == {}
    assert payload["experiment_stats"] is None
