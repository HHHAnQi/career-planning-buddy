"""PR-9c.2 calibration_loader tests.

Pins:
* Happy-path load of committed ``pairwise-calibration-fixture/v0-tests-fixture``
  (5-line committed test dataset)
* Source sha256 mismatch → CalibrationSourceHashMismatch
* pair_hash mismatch on a tampered line → CalibrationLineHashMismatch
* Unknown (dataset_id, dataset_version) → CalibrationDatasetNotFound
* Missing schema_version on a line → CalibrationLineSchemaError
* Output hash recomputation independently validated
* suggested_label flows through but is optional
* pair_count mismatch with manifest → CalibrationLoaderError
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from evals.v2.calibration_loader import (
    CalibrationDatasetBundle,
    CalibrationDatasetNotFound,
    CalibrationLineHashMismatch,
    CalibrationLineSchemaError,
    CalibrationLoaderError,
    CalibrationSourceHashMismatch,
    load_calibration_dataset,
)

# ---------------------------------------------------------- helpers


def _write_dataset(tmp_path: Path, lines: list[dict[str, Any]]) -> tuple[str, str, str]:
    """Write a fresh dataset under EVAL_ROOT/v2/datasets and return
    (dataset_id, dataset_version, manifest_filename).

    We monkeypatch ``DATASETS_DIR`` to a tmp path so committed datasets
    are not perturbed."""

    import evals.v2.calibration_loader as mod

    datasets_dir = tmp_path / "datasets"
    datasets_dir.mkdir(parents=True, exist_ok=True)
    jsonl_name = "smoke.jsonl"
    jsonl_path = datasets_dir / jsonl_name
    with jsonl_path.open("w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")
    source_sha = sha256(jsonl_path.read_bytes()).hexdigest()
    manifest = {
        "manifest_version": "2",
        "dataset_id": "smoke-tests",
        "dataset_version": "v0-fixture",
        "case_schema_version": "pairwise-calibration-export/v1",
        # ``source_path`` is resolved relative to EVAL_ROOT (which the
        # ``_restore_paths`` fixture patches to ``tmp_path``), so it must
        # echo the ``datasets/`` subdir.
        "source_path": "datasets/smoke.jsonl",
        "source_sha256": source_sha,
        "pair_count": len(lines),
    }
    manifest_path = datasets_dir / "manifest-smoke-tests-v0-fixture.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    # Patch module globals so the loader resolves manifest + source under tmp_path.
    mod.DATASETS_DIR = datasets_dir
    mod.EVAL_ROOT = tmp_path
    return "smoke-tests", "v0-fixture", jsonl_name


def _valid_line(idx: int) -> dict[str, Any]:
    """One completely-correct export line with re-validating hashes."""

    from evals.v2.contracts import canonical_sha256

    case_id = f"case-{idx}"
    request = {"expect_constraint": "c"}
    baseline_plan = {"summary": f"b-{idx}"}
    candidate_plan = {"summary": f"c-{idx}"}
    b_trial = f"00000000-0000-0000-0000-0000000000{idx:02x}"
    c_trial = f"00000000-0000-0000-0000-0000000001{idx:02x}"
    b_hash = canonical_sha256({"request": request, "plan": baseline_plan})
    c_hash = canonical_sha256({"request": request, "plan": candidate_plan})
    pair_hash = canonical_sha256({
        "schema_version": "eval-trial-pair/v1",
        "case_id": case_id,
        "baseline_trial_id": b_trial,
        "candidate_trial_id": c_trial,
        "baseline_output_hash": b_hash,
        "candidate_output_hash": c_hash,
    })
    return {
        "schema_version": "pairwise-calibration-export/v1",
        "pair_external_id": f"fx-{idx}",
        "case_id": case_id,
        "baseline_trial_id": b_trial,
        "candidate_trial_id": c_trial,
        "baseline_output_hash": b_hash,
        "candidate_output_hash": c_hash,
        "pair_hash": pair_hash,
        "suggested_label": None,
        "frozen_request_constraints": request,
        "frozen_baseline_plan_projection": baseline_plan,
        "frozen_candidate_plan_projection": candidate_plan,
        "frozen_rubric": [],
    }


@pytest.fixture(autouse=True)
def _restore_paths() -> Iterator[None]:
    import evals.v2.calibration_loader as mod
    orig_datasets = mod.DATASETS_DIR
    orig_eval_root = mod.EVAL_ROOT
    yield
    mod.DATASETS_DIR = orig_datasets
    mod.EVAL_ROOT = orig_eval_root


# ------------------------------------------------------- tests


def test_load_committed_fixture_dataset() -> None:
    bundle = load_calibration_dataset(
        dataset_id="pairwise-calibration-fixture",
        dataset_version="v0-tests-fixture",
    )
    assert isinstance(bundle, CalibrationDatasetBundle)
    assert len(bundle.lines) == 5
    assert all(line.schema_version == "pairwise-calibration-export/v1" for line in bundle.lines)
    assert bundle.manifest.dataset_id == "pairwise-calibration-fixture"
    # suggested_label defaults to None on every fixture line.
    assert all(line.suggested_label is None for line in bundle.lines)


def test_load_synthetic_happy_path(tmp_path: Path) -> None:
    dataset_id, version, _ = _write_dataset(tmp_path, [_valid_line(i) for i in range(3)])
    bundle = load_calibration_dataset(dataset_id=dataset_id, dataset_version=version)
    assert len(bundle.lines) == 3
    assert bundle.manifest.pair_count == 3


def test_source_sha_mismatch_rejected(tmp_path: Path) -> None:
    dataset_id, version, jsonl_name = _write_dataset(
        tmp_path, [_valid_line(i) for i in range(2)]
    )
    # Tamper with the source file post-manifest.
    (tmp_path / "datasets" / jsonl_name).write_text("tampered\n", encoding="utf-8")
    with pytest.raises(CalibrationSourceHashMismatch):
        load_calibration_dataset(dataset_id=dataset_id, dataset_version=version)


def test_pair_hash_tamper_rejected(tmp_path: Path) -> None:
    line = _valid_line(1)
    line["pair_hash"] = "0" * 64  # invalid sha for this line
    dataset_id, version, _ = _write_dataset(tmp_path, [line])
    with pytest.raises(CalibrationLineHashMismatch):
        load_calibration_dataset(dataset_id=dataset_id, dataset_version=version)


def test_baseline_output_hash_tamper_rejected(tmp_path: Path) -> None:
    line = _valid_line(1)
    line["baseline_output_hash"] = "f" * 64
    dataset_id, version, _ = _write_dataset(tmp_path, [line])
    with pytest.raises(CalibrationLineHashMismatch):
        load_calibration_dataset(dataset_id=dataset_id, dataset_version=version)


def test_candidate_output_hash_tamper_rejected(tmp_path: Path) -> None:
    line = _valid_line(1)
    line["candidate_output_hash"] = "e" * 64
    dataset_id, version, _ = _write_dataset(tmp_path, [line])
    with pytest.raises(CalibrationLineHashMismatch):
        load_calibration_dataset(dataset_id=dataset_id, dataset_version=version)


def test_unknown_dataset_raises(tmp_path: Path) -> None:
    _write_dataset(tmp_path, [_valid_line(1)])
    with pytest.raises(CalibrationDatasetNotFound):
        load_calibration_dataset(
            dataset_id="non-existent",
            dataset_version="v9",
        )


def test_missing_schema_version_rejected(tmp_path: Path) -> None:
    line = _valid_line(1)
    line["schema_version"] = "wrong/v2"
    dataset_id, version, _ = _write_dataset(tmp_path, [line])
    with pytest.raises(CalibrationLineSchemaError):
        load_calibration_dataset(dataset_id=dataset_id, dataset_version=version)


def test_pair_count_mismatch_rejected(tmp_path: Path) -> None:
    lines = [_valid_line(i) for i in range(2)]
    dataset_id, version, _ = _write_dataset(tmp_path, lines)
    # Now patch the manifest to a wrong pair_count.
    manifest_path = tmp_path / "datasets" / "manifest-smoke-tests-v0-fixture.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["pair_count"] = 99
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(CalibrationLoaderError):
        load_calibration_dataset(dataset_id=dataset_id, dataset_version=version)


def test_suggested_label_passes_through_when_set(tmp_path: Path) -> None:
    line = _valid_line(1)
    line["suggested_label"] = "baseline"
    dataset_id, version, _ = _write_dataset(tmp_path, [line])
    bundle = load_calibration_dataset(dataset_id=dataset_id, dataset_version=version)
    assert bundle.lines[0].suggested_label == "baseline"
