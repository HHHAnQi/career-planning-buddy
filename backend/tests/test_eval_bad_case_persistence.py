"""Tests for the Stage 5 runner's artifact / bad-case persistence contract.

Pins:
* The artifact JSON is always written.
* A fully passing experiment writes NO bad-case file (regression guard
  against empty marker files that previously polluted bad_cases/).
* An experiment with failed cases writes exactly the failed items as JSONL.
"""

from __future__ import annotations

import json

import pytest

import evals.runner as runner_module
from evals.runner import _persist_report


@pytest.fixture
def roots(tmp_path, monkeypatch):
    artifact_root = tmp_path / "artifacts"
    bad_case_root = tmp_path / "bad_cases"
    monkeypatch.setattr(runner_module, "ARTIFACT_ROOT", artifact_root)
    monkeypatch.setattr(runner_module, "BAD_CASE_ROOT", bad_case_root)
    return artifact_root, bad_case_root


def _report(results: list[dict[str, object]]) -> dict[str, object]:
    return {
        "experiment_id": "20260823T000000Z-test0001",
        "results": results,
    }


def test_passing_experiment_writes_artifact_but_no_bad_case(roots) -> None:
    artifact_root, bad_case_root = roots
    _persist_report(_report([{"case_id": "c1", "passed": True}]))
    assert (artifact_root / "20260823T000000Z-test0001.json").is_file()
    assert not bad_case_root.exists()


def test_failed_experiment_writes_only_failed_items(roots) -> None:
    _, bad_case_root = roots
    _persist_report(
        _report(
            [
                {"case_id": "c1", "passed": True},
                {"case_id": "c2", "passed": False, "graders": {"intent": False}},
            ]
        )
    )
    path = bad_case_root / "20260823T000000Z-test0001.jsonl"
    assert path.is_file()
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [record["case_id"] for record in records] == ["c2"]
    assert records[0]["passed"] is False


def test_missing_results_writes_no_bad_case(roots) -> None:
    _, bad_case_root = roots
    _persist_report({"experiment_id": "20260823T000000Z-test0002"})
    assert not bad_case_root.exists()
