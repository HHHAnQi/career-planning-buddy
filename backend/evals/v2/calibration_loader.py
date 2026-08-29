"""PR-9c.2 Calibration Export dataset loader.

A Calibration Export JSONL is the *frozen* Pair seed list a Sweep consumes
verbatim. The loader:

* resolves ``(dataset_id, dataset_version)`` under the controlled
  ``EVAL_ROOT/datasets/`` tree (no caller-supplied path per
  supplementary constraint #11 — ``manifest_path`` is FORBIDDEN);
* validates the manifest source sha256;
* validates that every JSONL line's ``pair_hash`` recomputes to the same
  value via the PR-9c.1 production formula;
* validates ``schema_version`` consistency;
* validates output-hash recomputation — frozen projection payloads are
  stored alongside the line and must hash back to the declared
  ``baseline_output_hash`` / ``candidate_output_hash``.

Calibration JSONL never contains ``human_label``. JSONL is NOT the source
of truth for human annotations — the DB is (supplementary constraint #8).
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.base import StrictModel
from evals.v2.contracts import canonical_sha256

EVAL_ROOT = Path(__file__).resolve().parents[1]
DATASETS_DIR = EVAL_ROOT / "v2" / "datasets"


class CalibrationExportLine(StrictModel):
    """One row of a calibration Export JSONL."""

    schema_version: str = Field(default="pairwise-calibration-export/v1")
    pair_external_id: str
    case_id: str
    baseline_trial_id: UUID
    candidate_trial_id: UUID
    baseline_output_hash: str
    candidate_output_hash: str
    pair_hash: str
    suggested_label: str | None = None
    frozen_request_constraints: dict[str, object] | None = None
    frozen_baseline_plan_projection: dict[str, object] | None = None
    frozen_candidate_plan_projection: dict[str, object] | None = None
    frozen_rubric: list[dict[str, object]] = Field(default_factory=list)

    @field_validator("baseline_output_hash", "candidate_output_hash", "pair_hash")
    @classmethod
    def _hash_must_be_sha256_hex(cls, value: str) -> str:
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError(f"expected 64-char hex hash, got {value!r}")
        return value


@dataclass(frozen=True)
class CalibrationDatasetBundle:
    """Validated, hydrated calibration dataset."""

    manifest: CalibrationExportManifest
    lines: list[CalibrationExportLine]
    source_sha256: str


class CalibrationExportManifest(StrictModel):
    """Manifest pinned to ``(dataset_id, dataset_version)`` under EVAL_ROOT."""

    manifest_version: str = Field(default="2")
    dataset_id: str
    dataset_version: str = Field(min_length=1)
    case_schema_version: str = Field(default="pairwise-calibration-export/v1")
    source_path: str
    source_sha256: str
    pair_count: int = Field(ge=0)

    @field_validator("source_sha256")
    @classmethod
    def _sha256_shape(cls, value: str) -> str:
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError(f"expected 64-char hex hash, got {value!r}")
        return value


class CalibrationLoaderError(ValueError):
    """Base class for all loader failures. Raised — never swallowed."""


class CalibrationSourceHashMismatch(CalibrationLoaderError):
    """On-disk JSONL bytes do not match manifest.source_sha256."""


class CalibrationSourcePathEscape(CalibrationLoaderError):
    """manifest.source_path tried to escape EVAL_ROOT."""


class CalibrationLineHashMismatch(CalibrationLoaderError):
    """A JSONL line's pair_hash / output_hash does not re-derive correctly."""


class CalibrationDatasetNotFound(CalibrationLoaderError):
    """No manifest file resolved for the requested (dataset_id, dataset_version)."""


class CalibrationLineSchemaError(CalibrationLoaderError):
    """A JSONL line failed strict schema validation."""


def _manifest_path_for(dataset_id: str, dataset_version: str) -> Path:
    """Controlled manifest resolution.

    The user explicitly forbade accepting ``manifest_path`` from callers.
    We resolve ``manifest-<dataset_id>-<dataset_version>.json`` under
    ``EVAL_ROOT/datasets/`` only.
    """

    safe_dataset_id = _safe_slug(dataset_id)
    safe_version = _safe_slug(dataset_version)
    candidate = DATASETS_DIR / f"manifest-{safe_dataset_id}-{safe_version}.json"
    if not candidate.is_file():
        raise CalibrationDatasetNotFound(
            f"no manifest for dataset_id={dataset_id!r} version={dataset_version!r}"
        )
    return candidate


def _safe_slug(value: str) -> str:
    if not value or any(c in value for c in ("/", "\\", "..", ":")):
        raise CalibrationLoaderError(f"unsafe dataset slug: {value!r}")
    return value


def load_calibration_dataset(
    *, dataset_id: str, dataset_version: str
) -> CalibrationDatasetBundle:
    """Public entry. Validates manifest sha + per-line pair_hash."""

    manifest_path = _manifest_path_for(dataset_id, dataset_version)
    manifest = CalibrationExportManifest.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    if manifest.dataset_id != dataset_id or manifest.dataset_version != dataset_version:
        raise CalibrationLoaderError(
            "manifest identity fields must match request: "
            f"got {manifest.dataset_id}/{manifest.dataset_version}"
        )

    source_path = _resolve_source_under_eval_root(manifest.source_path)
    source_bytes = source_path.read_bytes()
    actual_sha = sha256(source_bytes).hexdigest()
    if actual_sha != manifest.source_sha256:
        raise CalibrationSourceHashMismatch(
            f"calibration source sha256 mismatch: manifest={manifest.source_sha256}, "
            f"actual={actual_sha}"
        )

    lines = _parse_lines(source_bytes)
    if len(lines) != manifest.pair_count:
        raise CalibrationLoaderError(
            f"manifest pair_count={manifest.pair_count} but jsonl has {len(lines)} lines"
        )
    for line in lines:
        _validate_line(line)

    return CalibrationDatasetBundle(
        manifest=manifest,
        lines=lines,
        source_sha256=manifest.source_sha256,
    )


def _resolve_source_under_eval_root(source_path: str) -> Path:
    """Resolve ``manifest.source_path`` strictly under ``EVAL_ROOT``.

    Calqued from ``evals.v2.dataset_loader._resolve_source_under_eval_root``.
    """

    resolved = (EVAL_ROOT / source_path).resolve()
    try:
        resolved.relative_to(EVAL_ROOT)
    except ValueError as exc:
        raise CalibrationSourcePathEscape(
            f"source_path escapes EVAL_ROOT: {source_path!r}"
        ) from exc
    return resolved


def _parse_lines(source_bytes: bytes) -> list[CalibrationExportLine]:
    text = source_bytes.decode("utf-8")
    lines: list[CalibrationExportLine] = []
    for line_no, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            parsed = CalibrationExportLine.model_validate_json(raw)
        except ValueError as exc:
            raise CalibrationLineSchemaError(
                f"line {line_no} schema error: {exc}"
            ) from exc
        lines.append(parsed)
    return lines


def _validate_line(line: CalibrationExportLine) -> None:
    """Independently re-derive output + pair hashes for one line."""

    if line.schema_version != "pairwise-calibration-export/v1":
        raise CalibrationLineSchemaError(
            f"unsupported schema_version on line {line.pair_external_id}: "
            f"{line.schema_version!r}"
        )

    baseline_output_hash = _recompute_output_hash(
        request_constraints=line.frozen_request_constraints,
        plan_projection=line.frozen_baseline_plan_projection,
    )
    if baseline_output_hash != line.baseline_output_hash:
        raise CalibrationLineHashMismatch(
            f"baseline_output_hash mismatch on {line.pair_external_id}"
        )

    candidate_output_hash = _recompute_output_hash(
        request_constraints=line.frozen_request_constraints,
        plan_projection=line.frozen_candidate_plan_projection,
    )
    if candidate_output_hash != line.candidate_output_hash:
        raise CalibrationLineHashMismatch(
            f"candidate_output_hash mismatch on {line.pair_external_id}"
        )

    pair_hash = canonical_sha256({
        "schema_version": "eval-trial-pair/v1",
        "case_id": line.case_id,
        "baseline_trial_id": str(line.baseline_trial_id),
        "candidate_trial_id": str(line.candidate_trial_id),
        "baseline_output_hash": baseline_output_hash,
        "candidate_output_hash": candidate_output_hash,
    })
    if pair_hash != line.pair_hash:
        raise CalibrationLineHashMismatch(
            f"pair_hash mismatch on {line.pair_external_id}"
        )


def _recompute_output_hash(
    *,
    request_constraints: dict[str, object] | None,
    plan_projection: dict[str, object] | None,
) -> str:
    """Replay ``TrialEvidenceProjection.as_display()`` + ``canonical_sha256``."""

    payload: dict[str, object] = {}
    if request_constraints is not None:
        payload["request"] = request_constraints
    if plan_projection is not None:
        payload["plan"] = plan_projection
    return canonical_sha256(payload)


__all__ = [
    "CalibrationDatasetBundle",
    "CalibrationDatasetNotFound",
    "CalibrationExportLine",
    "CalibrationExportManifest",
    "CalibrationLineHashMismatch",
    "CalibrationLineSchemaError",
    "CalibrationLoaderError",
    "CalibrationSourceHashMismatch",
    "CalibrationSourcePathEscape",
    "load_calibration_dataset",
]
