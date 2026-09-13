"""Tests for ``scripts/build_pairwise_calibration_v1.py``.

Pins:
* Dry-run reports ``gate_unmet`` when human annotations < 100.
* Non-dry-run raises ``CalibrationGateUnmet`` and writes nothing.
* Per-row validations raise their named exceptions:
  - ``MissingProjectionError``
  - ``DegeneratePairError``
  - ``HashDriftError`` (output_hash and pair_hash)
  - ``DuplicatePairHashError``
  - ``ForbiddenFieldError``
* End-to-end build with the gate threshold lowered writes a loadable
  v1 dataset; output is byte-idempotent across re-runs.
* The loader's ``StrictModel(extra="forbid")`` rejects any row carrying
  ``human_label`` / ``winner`` / etc. — regression guard for the
  forbidden-field contract.

The DB-bound tests provision their own Stage B-1 experiments
(`compact_execution_v1` vs `structured_reasoning_v1`) so they remain
reproducible against the empty PostgreSQL database used by CI.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

from app.core.config import get_settings
from app.models.eval import EvalTrialPair
from scripts.build_pairwise_calibration_v1 import (
    DATASET_ID,
    DATASET_VERSION,
    DegeneratePairError,
    DuplicatePairHashError,
    ForbiddenFieldError,
    HashDriftError,
    MissingProjectionError,
    _check_duplicate_pair_hashes,
    _validate_row,
)


# Restore the loader's path module-attributes after every test so the
# DB-bound tests' monkeypatching of DATASETS_DIR / EVAL_ROOT does not
# leak into sibling calibration_loader tests (which read committed
# fixture datasets from the real EVAL_ROOT).
@pytest.fixture(autouse=True)
def _restore_loader_paths() -> Iterator[None]:
    import evals.v2.calibration_loader as mod

    orig_datasets = mod.DATASETS_DIR
    orig_eval_root = mod.EVAL_ROOT
    yield
    mod.DATASETS_DIR = orig_datasets
    mod.EVAL_ROOT = orig_eval_root

# ---------------------------------------------------------- helpers


def _pair_stub(
    *,
    case_id: str = "create-01",
    baseline_trial_id: UUID | None = None,
    candidate_trial_id: UUID | None = None,
    pair_id: UUID | None = None,
) -> EvalTrialPair:
    """Lightweight stand-in for ``EvalTrialPair`` — only the attributes
    ``_validate_row`` reads (``id``, ``case_id``, ``baseline_trial_id``,
    ``candidate_trial_id``). Avoids a round-trip to the DB for the
    pure-logic validation tests."""

    return cast(
        EvalTrialPair,
        SimpleNamespace(
            id=pair_id or uuid4(),
            case_id=case_id,
            baseline_trial_id=baseline_trial_id or uuid4(),
            candidate_trial_id=candidate_trial_id or uuid4(),
        ),
    )


def _valid_row(
    *,
    request: dict[str, Any] | None = None,
    baseline_plan: dict[str, Any] | None = None,
    candidate_plan: dict[str, Any] | None = None,
    pair: EvalTrialPair | None = None,
) -> dict[str, Any]:
    """Return a row that passes all 5 validations. Caller can mutate
    fields before invoking ``_validate_row`` to trigger a specific
    failure mode."""

    from evals.v2.contracts import canonical_sha256

    pair = pair or _pair_stub()
    request = request or {"user_request": "create a plan"}
    baseline_plan = baseline_plan or {"summary": "compact_v1 plan"}
    candidate_plan = candidate_plan or {"summary": "structured_v1 plan"}

    b_hash = canonical_sha256({"request": request, "plan": baseline_plan})
    c_hash = canonical_sha256({"request": request, "plan": candidate_plan})
    pair_hash = canonical_sha256(
        {
            "schema_version": "eval-trial-pair/v1",
            "case_id": pair.case_id,
            "baseline_trial_id": str(pair.baseline_trial_id),
            "candidate_trial_id": str(pair.candidate_trial_id),
            "baseline_output_hash": b_hash,
            "candidate_output_hash": c_hash,
        }
    )
    return {
        "schema_version": "pairwise-calibration-export/v1",
        "pair_external_id": "v1-0000-create-01",
        "case_id": pair.case_id,
        "baseline_trial_id": str(pair.baseline_trial_id),
        "candidate_trial_id": str(pair.candidate_trial_id),
        "baseline_output_hash": b_hash,
        "candidate_output_hash": c_hash,
        "pair_hash": pair_hash,
        "suggested_label": None,
        "frozen_request_constraints": request,
        "frozen_baseline_plan_projection": baseline_plan,
        "frozen_candidate_plan_projection": candidate_plan,
        "frozen_rubric": [{"criterion_id": "c1", "description": "有可执行步骤"}],
    }


# ------------------------------------------------------- pure-logic tests


def test_validate_row_accepts_well_formed_row() -> None:
    """Sanity: a row built via ``_valid_row`` passes all 5 checks."""
    pair = _pair_stub()
    _validate_row(_valid_row(pair=pair), pair=pair)  # no exception


def test_validate_row_rejects_missing_projection() -> None:
    pair = _pair_stub()
    row = _valid_row(pair=pair)
    row["frozen_baseline_plan_projection"] = None
    with pytest.raises(MissingProjectionError) as exc_info:
        _validate_row(row, pair=pair)
    assert "frozen_baseline_plan_projection" in exc_info.value.kinds


def test_validate_row_rejects_degenerate_pair() -> None:
    """Identical baseline + candidate projections ⇒ no comparison signal."""
    pair = _pair_stub()
    same_plan = {"summary": "same plan both sides"}
    row = _valid_row(
        pair=pair, baseline_plan=same_plan, candidate_plan=same_plan
    )
    with pytest.raises(DegeneratePairError):
        _validate_row(row, pair=pair)


def test_validate_row_rejects_output_hash_drift() -> None:
    pair = _pair_stub()
    row = _valid_row(pair=pair)
    row["baseline_output_hash"] = "0" * 64
    with pytest.raises(HashDriftError) as exc_info:
        _validate_row(row, pair=pair)
    assert exc_info.value.field == "baseline_output_hash"


def test_validate_row_rejects_pair_hash_drift() -> None:
    pair = _pair_stub()
    row = _valid_row(pair=pair)
    row["pair_hash"] = "0" * 64
    with pytest.raises(HashDriftError) as exc_info:
        _validate_row(row, pair=pair)
    assert exc_info.value.field == "pair_hash"


def test_validate_row_rejects_forbidden_field() -> None:
    pair = _pair_stub()
    row = _valid_row(pair=pair)
    row["human_label"] = "a"
    with pytest.raises(ForbiddenFieldError) as exc_info:
        _validate_row(row, pair=pair)
    assert exc_info.value.field == "human_label"


def test_check_duplicate_pair_hashes_rejects_collision() -> None:
    row_a = _valid_row()
    row_b = _valid_row()
    row_b["pair_hash"] = row_a["pair_hash"]
    with pytest.raises(DuplicatePairHashError) as exc_info:
        _check_duplicate_pair_hashes([row_a, row_b])
    assert exc_info.value.pair_hash == row_a["pair_hash"]


# ------------------------------------------- loader-side forbidden contract


def test_loader_rejects_row_with_human_label() -> None:
    """Regression: ``CalibrationExportLine`` uses ``StrictModel(
    extra="forbid")``; a row carrying any human-label-shaped field must
    fail to parse. Pins the contract that v1 dataset rows can NEVER
    carry labels, even if a future exporter accidentally emits them.

    The loader's batch path (``_parse_lines``) wraps this as
    ``CalibrationLineSchemaError``; here we hit the model directly so
    the test stays a tight unit-promise on the schema itself."""

    from pydantic import ValidationError

    from evals.v2.calibration_loader import CalibrationExportLine

    row = _valid_row()
    row["human_label"] = "a"
    with pytest.raises(ValidationError) as exc_info:
        CalibrationExportLine.model_validate(row)
    assert "extra_forbidden" in str(exc_info.value)


# ----------------------------------------------------- DB-bound gate tests
#
# These provision 23 variant-backed pairs with no human annotations, then
# confirm the formal gate and dry-run behaviour against that isolated data.


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def stage_b1_experiment_ids() -> tuple[UUID, UUID]:
    """Create the Stage B-1 comparison data required by the builder tests."""

    from scripts.stage_b1_provision import provision

    settings = get_settings().model_copy(
        update={
            "llm_provider": "mock",
            "eval_provider_mode": "fixture",
            "search_provider": "mock",
            "embedding_provider": "mock",
        }
    )
    outcome = await provision(settings=settings)
    assert outcome["ok"] is True
    return (
        UUID(str(outcome["baseline_experiment_id"])),
        UUID(str(outcome["candidate_experiment_id"])),
    )


@pytest.mark.asyncio
async def test_builder_dry_run_reports_gate_unmet_on_stage_b1(
    stage_b1_experiment_ids: tuple[UUID, UUID], tmp_path: Path
) -> None:
    """Dry-run on the current Stage B-1 pairs (0 human annotations)
    returns ``{ok: False, reason: "gate_unmet", valid: 0, required: 100}``
    and writes nothing to disk."""

    from scripts import build_pairwise_calibration_v1 as builder

    baseline_id, candidate_id = stage_b1_experiment_ids
    # Ensure the build path doesn't touch committed datasets.
    out = await builder.build_v1(
        baseline_experiment_id=baseline_id,
        candidate_experiment_id=candidate_id,
        datasets_dir=tmp_path,
        dry_run=True,
    )
    assert out["ok"] is False
    assert out["reason"] == "gate_unmet"
    assert out["valid_human_pair_count"] == 0
    assert out["required"] == 100
    # No file was written.
    assert not (tmp_path / f"{DATASET_ID}.jsonl").exists()


@pytest.mark.asyncio
async def test_builder_refuses_to_write_v1_below_gate(
    stage_b1_experiment_ids: tuple[UUID, UUID], tmp_path: Path
) -> None:
    """Non-dry-run below the gate raises CalibrationGateUnmet and
    writes no JSONL."""

    from scripts.build_pairwise_calibration_v1 import CalibrationGateUnmet

    with pytest.raises(CalibrationGateUnmet):
        await build_v1_for_test(tmp_path, stage_b1_experiment_ids)
    assert not (tmp_path / f"{DATASET_ID}.jsonl").exists()


async def build_v1_for_test(
    tmp_path: Path, experiment_ids: tuple[UUID, UUID]
) -> dict[str, Any]:
    from scripts.build_pairwise_calibration_v1 import build_v1

    baseline_id, candidate_id = experiment_ids
    return await build_v1(
        baseline_experiment_id=baseline_id,
        candidate_experiment_id=candidate_id,
        datasets_dir=tmp_path,
        dry_run=False,
    )


@pytest.mark.asyncio
async def test_builder_emits_v1_and_round_trips_when_gate_passes(
    monkeypatch: pytest.MonkeyPatch,
    stage_b1_experiment_ids: tuple[UUID, UUID],
    tmp_path: Path,
) -> None:
    """Lower the gate threshold to 0 so the build actually emits; verify
    the output round-trips the loader and carries no forbidden fields.

    The 23 Stage B-1 pairs have real divergent baseline/candidate
    projections, so this exercises the full write+load path with real
    data — without fabricating 100 fake annotations.
    """

    from scripts import build_pairwise_calibration_v1 as builder

    monkeypatch.setattr(builder, "MIN_VALID_HUMAN_PAIRS", 0)

    # Match the loader's default layout: EVAL_ROOT = DATASETS_DIR.parent.
    # The builder threads ``eval_root=datasets_dir.parent`` into
    # ``write_dataset``; we mirror the same value here so the round-trip
    # load finds the freshly written file.
    import evals.v2.calibration_loader as loader_mod

    loader_mod.DATASETS_DIR = tmp_path
    loader_mod.EVAL_ROOT = tmp_path.parent

    baseline_id, candidate_id = stage_b1_experiment_ids
    out = await builder.build_v1(
        baseline_experiment_id=baseline_id,
        candidate_experiment_id=candidate_id,
        datasets_dir=tmp_path,
        dry_run=False,
    )
    assert out["ok"] is True, f"build failed: {out}"
    assert out["pair_count"] >= 20
    assert out["dataset_id"] == DATASET_ID

    # Round-trip load — proves loader accepts what we wrote.
    bundle = loader_mod.load_calibration_dataset(
        dataset_id=DATASET_ID, dataset_version=DATASET_VERSION
    )
    assert len(bundle.lines) == out["pair_count"]

    # No row carries a forbidden field.
    for line in bundle.lines:
        line_dict = line.model_dump()
        for forbidden in (
            "reviewer_id", "winner", "human_label", "adjudication",
            "adjudication_result", "label", "annotation",
        ):
            assert forbidden not in line_dict, (
                f"forbidden field {forbidden!r} present in v1 row"
            )


@pytest.mark.asyncio
async def test_builder_output_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    stage_b1_experiment_ids: tuple[UUID, UUID],
    tmp_path: Path,
) -> None:
    """Two consecutive builds into different dirs produce byte-identical
    JSONL and manifest — the builder is a pure function of DB state."""

    from scripts import build_pairwise_calibration_v1 as builder

    monkeypatch.setattr(builder, "MIN_VALID_HUMAN_PAIRS", 0)
    import evals.v2.calibration_loader as loader_mod

    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()

    # Point the loader at dir_a for the first build's round-trip, then
    # dir_b for the second. The builder's own round-trip assertion does
    # the verification.
    loader_mod.DATASETS_DIR = dir_a
    loader_mod.EVAL_ROOT = dir_a.parent
    baseline_id, candidate_id = stage_b1_experiment_ids
    out_a = await builder.build_v1(
        baseline_experiment_id=baseline_id,
        candidate_experiment_id=candidate_id,
        datasets_dir=dir_a,
        dry_run=False,
    )
    assert out_a["ok"]

    loader_mod.DATASETS_DIR = dir_b
    loader_mod.EVAL_ROOT = dir_b.parent
    out_b = await builder.build_v1(
        baseline_experiment_id=baseline_id,
        candidate_experiment_id=candidate_id,
        datasets_dir=dir_b,
        dry_run=False,
    )
    assert out_b["ok"]

    jsonl_a = (dir_a / f"{DATASET_ID}.jsonl").read_bytes()
    jsonl_b = (dir_b / f"{DATASET_ID}.jsonl").read_bytes()
    assert jsonl_a == jsonl_b, "JSONL not byte-identical across runs"

    # Manifest ``source_path`` is intentionally dir-relative (so the
    # loader can resolve it under EVAL_ROOT); it differs across the two
    # output dirs by design. Compare the manifest payloads EXCEPT
    # source_path, which must be byte-identical.
    import json as _json

    def _manifest_payload_minus_source(path: Path) -> dict[str, Any]:
        data = cast(dict[str, Any], _json.loads(path.read_text(encoding="utf-8")))
        data.pop("source_path", None)
        return data

    manifest_a_payload = _manifest_payload_minus_source(
        dir_a / f"manifest-{DATASET_ID}-{DATASET_VERSION}.json"
    )
    manifest_b_payload = _manifest_payload_minus_source(
        dir_b / f"manifest-{DATASET_ID}-{DATASET_VERSION}.json"
    )
    assert manifest_a_payload == manifest_b_payload, (
        f"manifest payload (excluding source_path) differs: "
        f"{manifest_a_payload} vs {manifest_b_payload}"
    )

    # And the source_sha256 field agrees (it's a hash of JSONL bytes,
    # which are identical, so this is a stronger invariant).
    assert out_a["source_sha256"] == out_b["source_sha256"]
