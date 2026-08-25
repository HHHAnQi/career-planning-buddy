"""Strict Dataset and Grade contract tests."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from evals.v2.contracts import EvalCase, ExperimentCreate, GradeResult
from evals.v2.dataset_loader import DEFAULT_MANIFEST_PATH, load_dataset


def test_stage5_adapter_loads_thirty_hash_verified_v2_cases() -> None:
    bundle = load_dataset()

    assert bundle.manifest.dataset_id == "stage5"
    assert len(bundle.cases) == 33  # 30 mock + 3 live-only
    assert len({case.case_id for case in bundle.cases}) == 33
    assert all(case.schema_version == "2" for case in bundle.cases)
    assert all(len(case.fixture_hash) == 64 for case in bundle.cases)


def test_stage5_adapter_preserves_fallback_plan_terminal_contract() -> None:
    cases = {case.case_id: case for case in load_dataset().cases}

    assert cases["repair-02"].expected_outcome.result_kind == "plan"
    assert cases["repair-02"].expected_outcome.allowed_run_statuses == ["degraded"]
    assert cases["repair-04"].expected_outcome.result_kind == "plan"
    assert cases["repair-04"].expected_outcome.allowed_run_statuses == ["degraded"]
    assert cases["create-01"].expected_outcome.allowed_run_statuses == ["completed"]


def test_eval_case_rejects_unknown_fields_and_fixture_tampering() -> None:
    case_payload = load_dataset().cases[0].model_dump(mode="json")
    case_payload["unknown"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EvalCase.model_validate(case_payload)

    case_payload.pop("unknown")
    case_payload["scenario"]["user_request"] = "tampered after fixture hash was frozen"
    with pytest.raises(ValidationError, match="fixture_hash mismatch"):
        EvalCase.model_validate(case_payload)


def test_loader_rejects_manifest_hash_mismatch(tmp_path: Path) -> None:
    manifest = json.loads(DEFAULT_MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["source_sha256"] = "0" * 64
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="dataset hash mismatch"):
        load_dataset(path)


def test_experiment_requires_all_versions_and_rejects_self_baseline() -> None:
    values = _experiment_values()
    values["graph_version"] = ""
    with pytest.raises(ValidationError, match="at least 1 character"):
        ExperimentCreate.model_validate(values)

    experiment_id = uuid4()
    values = _experiment_values()
    values.update(
        experiment_id=experiment_id,
        variant_role="candidate",
        baseline_experiment_id=experiment_id,
    )
    with pytest.raises(ValidationError, match="cannot use itself as baseline"):
        ExperimentCreate.model_validate(values)


@pytest.mark.parametrize(
    ("metric_type", "values"),
    [
        ("boolean", {"passed": True}),
        ("numeric", {"score": 0.75, "passed": True, "threshold": 0.7}),
        ("categorical", {"categorical_value": "safe", "passed": True}),
    ],
)
def test_grade_result_preserves_metric_type(metric_type: str, values: dict[str, object]) -> None:
    grade = GradeResult(
        grader_name="contract",
        grader_version="v1",
        domain="system",
        metric_type=metric_type,
        hard_gate=True,
        evidence={},
        **values,
    )
    assert grade.metric_type == metric_type


def test_grade_result_rejects_ambiguous_typed_value() -> None:
    with pytest.raises(ValidationError, match="invalid value fields"):
        GradeResult(
            grader_name="contract",
            grader_version="v1",
            domain="system",
            metric_type="boolean",
            passed=True,
            score=1.0,
            hard_gate=True,
            evidence={},
        )


def _experiment_values() -> dict[str, object]:
    manifest = load_dataset().manifest
    return {
        "dataset_id": manifest.dataset_id,
        "dataset_version": manifest.dataset_version,
        "dataset_hash": manifest.source_sha256,
        "git_commit": "7d29a45",
        "graph_version": "stage5-v1",
        "prompt_version": "career-plan-v1",
        "model_version": "mock-v1",
        "tool_version": "tool-contract-v1",
        "context_version": "context-v1",
        "memory_version": "memory-v1",
        "execution_mode": "mock_provider",
        "variant_role": "baseline",
        "trial_count": 1,
    }
