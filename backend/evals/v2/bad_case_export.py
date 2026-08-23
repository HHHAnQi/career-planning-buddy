"""Bad-case export for Eval Harness V2 experiments.

A bad case is a Trial that did not complete (runtime failure) or a case whose
completed Trials failed any hard gate (grading failure). Records are written
as one JSON line each to ``evals/bad_cases/{experiment_id}.jsonl`` so a
failure can be reproduced from the persisted Experiment, Trial, and Trace.

A file is written ONLY when the experiment actually produced failures; fully
passing experiments leave no empty marker files behind.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from evals.v2.experiment_runner import ExperimentReport

EVAL_ROOT = Path(__file__).resolve().parent.parent
BAD_CASE_ROOT = EVAL_ROOT / "bad_cases"


def _runtime_failure_records(report: ExperimentReport) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for trial in report.trials:
        if trial.status == "cancelled":
            # "cancelled" is user-intentional (its own CaseStat bucket) and
            # not a defect worth reproducing.
            continue
        if trial.status == "completed" and trial.error_code is None:
            continue
        records.append(
            {
                "experiment_id": str(report.experiment_id),
                "trial_id": str(trial.trial_id),
                "case_id": trial.case_id,
                "failure_kind": "runtime_failure",
                "trial_status": trial.status,
                "run_status": trial.run_status,
                "result_kind": trial.result_kind,
                "error_code": trial.error_code,
                "tool_call_count": trial.tool_call_count,
                "captured_at": datetime.now(UTC).isoformat(),
            }
        )
    return records


def _hard_gate_failure_records(report: ExperimentReport) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for case_id, stat in sorted(report.case_stats.items()):
        if stat.completed_count == 0:
            continue
        if stat.hard_gate_passed_count >= stat.completed_count:
            continue
        records.append(
            {
                "experiment_id": str(report.experiment_id),
                "case_id": case_id,
                "failure_kind": "hard_gate_failure",
                "trial_count": stat.trial_count,
                "completed_count": stat.completed_count,
                "hard_gate_passed_count": stat.hard_gate_passed_count,
                "runtime_failure_count": stat.runtime_failure_count,
                "first_attempt_passed": stat.first_attempt_passed,
                "captured_at": datetime.now(UTC).isoformat(),
            }
        )
    return records


def write_bad_cases(report: ExperimentReport) -> Path | None:
    """Persist failures of ``report`` as bad-case JSONL; return path or None."""

    records = _runtime_failure_records(report) + _hard_gate_failure_records(report)
    if not records:
        return None
    BAD_CASE_ROOT.mkdir(parents=True, exist_ok=True)
    path = BAD_CASE_ROOT / f"{report.experiment_id}.jsonl"
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    return path


__all__ = ["BAD_CASE_ROOT", "write_bad_cases"]
