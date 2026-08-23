"""Tests for ``evals.v2.bad_case_export``.

Pins:
* Fully passing experiments write NO bad-case file (no empty markers).
* A Trial that did not complete produces a ``runtime_failure`` record.
* A case whose completed Trials missed a hard gate produces a
  ``hard_gate_failure`` record even when every Trial "completed".
* Records are valid JSONL keyed by the experiment id.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from evals.v2 import bad_case_export
from evals.v2.bad_case_export import write_bad_cases
from evals.v2.experiment_runner import ExperimentReport, TrialSummary
from evals.v2.stats import CaseStat, CIInterval


def _trial(*, status: str = "completed", error_code: str | None = None) -> TrialSummary:
    return TrialSummary(
        trial_id=uuid4(),
        case_id="case-001",
        status=status,
        run_status="completed" if status == "completed" else "failed",
        result_kind="plan_created",
        tokens_in=100,
        tokens_out=50,
        latency_ms=200,
        error_code=error_code,
        terminal_event_count=1,
        tool_call_count=0,
    )


def _case_stat(*, completed: int, hard_gate_passed: int) -> CaseStat:
    return CaseStat(
        case_id="case-001",
        trial_count=completed,
        completed_count=completed,
        hard_gate_passed_count=hard_gate_passed,
        runtime_failure_count=0,
        configuration_failure_count=0,
        cancelled_by_user_count=0,
        first_attempt_passed=hard_gate_passed == completed,
        pass_at_n=hard_gate_passed > 0,
        pass_all_n=hard_gate_passed == completed,
        success_rate=hard_gate_passed / completed if completed else 0.0,
        success_rate_ci=CIInterval(low=0.0, high=1.0),
        mean_tokens_in=100.0,
        mean_tokens_out=50.0,
        mean_latency_ms=200.0,
        tokens_in_ci=CIInterval(low=90.0, high=110.0),
        tokens_out_ci=CIInterval(low=40.0, high=60.0),
        latency_ci=CIInterval(low=180.0, high=220.0),
    )


def _report(
    *,
    trials: list[TrialSummary],
    case_stats: dict[str, CaseStat] | None = None,
) -> ExperimentReport:
    return ExperimentReport(
        experiment_id=uuid4(),
        experiment_status="completed",
        trial_count=len(trials),
        trials=trials,
        case_stats=case_stats or {},
    )


@pytest.fixture
def bad_case_root(tmp_path, monkeypatch):
    root = tmp_path / "bad_cases"
    monkeypatch.setattr(bad_case_export, "BAD_CASE_ROOT", root)
    return root


def test_passing_experiment_writes_no_file(bad_case_root) -> None:
    report = _report(
        trials=[_trial()],
        case_stats={"case-001": _case_stat(completed=1, hard_gate_passed=1)},
    )
    assert write_bad_cases(report) is None
    assert not bad_case_root.exists()


def test_runtime_failure_trial_is_exported(bad_case_root) -> None:
    report = _report(
        trials=[
            _trial(),
            _trial(status="failed", error_code="budget_exceeded"),
        ]
    )
    path = write_bad_cases(report)
    assert path is not None and path.is_file()
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    record = records[0]
    assert record["failure_kind"] == "runtime_failure"
    assert record["case_id"] == "case-001"
    assert record["error_code"] == "budget_exceeded"
    assert record["experiment_id"] == str(report.experiment_id)


def test_hard_gate_failure_case_is_exported(bad_case_root) -> None:
    report = _report(
        trials=[_trial()],
        case_stats={"case-001": _case_stat(completed=2, hard_gate_passed=1)},
    )
    path = write_bad_cases(report)
    assert path is not None
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert records[0]["failure_kind"] == "hard_gate_failure"
    assert records[0]["completed_count"] == 2
    assert records[0]["hard_gate_passed_count"] == 1


def test_file_name_is_experiment_id(bad_case_root) -> None:
    report = _report(trials=[_trial(status="failed", error_code="provider_error")])
    path = write_bad_cases(report)
    assert path is not None
    assert path.name == f"{report.experiment_id}.jsonl"


def test_cancelled_trial_is_not_a_bad_case(bad_case_root) -> None:
    report = _report(trials=[_trial(status="cancelled", error_code="RUN_CANCELLED")])
    assert write_bad_cases(report) is None
    assert not bad_case_root.exists()
