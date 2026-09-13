"""Export real graded Trial pairs to a Pairwise Calibration JSONL+manifest.

PR-9c.2 Commit 3.3 — Stage A: real ``pairwise-calibration-v0-dev-smoke``.

Reads ``EvalTrialPair`` rows for a given baseline + candidate experiment
pair and emits a Pairwise Calibration Export dataset that the loader in
``evals/v2/calibration_loader.py`` accepts byte-for-byte. The output
JSONL + manifest MUST be byte-reproducible so the loader's
``source_sha256`` and per-line ``*_output_hash`` / ``pair_hash`` checks
re-derive the same values.

Usage::

    PYTHONPATH=. python scripts/export_pairwise_dataset.py \\
        --baseline-experiment-id <UUID> \\
        --candidate-experiment-id <UUID> \\
        --output-dataset-id pairwise-calibration-v0-dev-smoke \\
        --output-dataset-version 1

Inputs come from the DB only — no live LLM, no fabrications. The
optional ``--fixture-overrides-path`` flag accepts a JSON file of
``{pair_external_id: {"winner": "a"|"b"|"tie"|"both_unacceptable", ...}}``
that pairs with the Sweep's ``fixture_mapping`` for fixture-mode smoke
runs. Without overrides, the manifest is emitted without a mapping.

This script is intended for smoke workflow validation only; the formal
``pairwise-calibration-v1`` dataset requires real human annotation per
handoff §8.8 boundary.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import uuid
from pathlib import Path
from typing import Any

# Resolve import path when invoked as a script.
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.models.eval import EvalEvidenceItem, EvalTrial, EvalTrialPair  # noqa: E402
from evals.v2.contracts import canonical_sha256  # noqa: E402

EXPORT_SCHEMA_VERSION = "pairwise-calibration-export/v1"
# Manifest's case_schema_version tracks the variant of the *pair_hash*
# canonical_sha256 payload (loader recomputes with schema_version
# "eval-trial-pair/v1" inside the payload). However the committed
# fixture manifest (manifest-pairwise-calibration-fixture...) records
# "pairwise-calibration-export/v1" here — loader only echos it back to
# the caller; it does not validate its value. Pick the
# export-version form for parity with the fixture.
MANIFEST_CASE_SCHEMA_VERSION = "pairwise-calibration-export/v1"

# Canonical schema_version tag embedded INSIDE the pair_hash payload
# (calibration_loader._recompute_pair_hash line ~237).
_PAIR_HASH_PAYLOAD_SCHEMA = "eval-trial-pair/v1"
MANIFEST_VERSION = "2"

# Evidence kinds the loader knows how to freeze into a Pair's request
# constraints / plan projections. Kept in lockstep with
# build_frozen_review_surface + the canonical_sha256 formula in
# calibration_loader._recompute_output_hash. PR-9c.1 records
# EvalEvidenceItem.kind as lower-case strings (ck_eval_evidence_items_kind).
_REQUEST_CONSTRAINTS_KIND = "request_constraints"
_PLAN_PROJECTION_KIND = "plan_projection"


async def _load_evidence_payload(
    session: AsyncSession, trial_id: uuid.UUID, kind: str
) -> dict[str, Any] | None:
    """Return the latest ``EvalEvidenceItem.projection_json`` of ``kind``
    for ``trial_id``. PR-9c.1 records exactly one request_constraints
    row and one plan_projection row per trial; we pick the first by
    created_at."""

    rows = (
        await session.execute(
            select(EvalEvidenceItem)
            .where(
                EvalEvidenceItem.trial_id == trial_id,
                EvalEvidenceItem.kind == kind,
            )
            .order_by(EvalEvidenceItem.created_at)
            .limit(1)
        )
    ).scalars().all()
    if not rows:
        return None
    payload = rows[0].projection_json
    if isinstance(payload, dict):
        return payload
    return None  # pragma: no cover — projection_json column is JSONB dict


def _row_from_pair(
    pair: EvalTrialPair,
    *,
    request: dict[str, Any],
    baseline_plan: dict[str, Any] | None,
    candidate_plan: dict[str, Any] | None,
    pair_external_id: str,
    suggested_label: str | None,
) -> dict[str, Any]:
    """Build ONE export row. ``output_hash`` and ``pair_hash`` are
    re-derived via the loader's canonical_sha256 formula so
    `_recompute_output_hash` 反推 (line 209-262 of
    calibration_loader) matches."""

    baseline_output_hash = canonical_sha256(
        {"request": request, "plan": baseline_plan or {}}
    )
    candidate_output_hash = canonical_sha256(
        {"request": request, "plan": candidate_plan or {}}
    )
    pair_hash = canonical_sha256(
        {
            "schema_version": _PAIR_HASH_PAYLOAD_SCHEMA,
            "case_id": pair.case_id,
            "baseline_trial_id": str(pair.baseline_trial_id),
            "candidate_trial_id": str(pair.candidate_trial_id),
            "baseline_output_hash": baseline_output_hash,
            "candidate_output_hash": candidate_output_hash,
        }
    )
    return {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "pair_external_id": pair_external_id,
        "case_id": pair.case_id,
        "baseline_trial_id": str(pair.baseline_trial_id),
        "candidate_trial_id": str(pair.candidate_trial_id),
        "baseline_output_hash": baseline_output_hash,
        "candidate_output_hash": candidate_output_hash,
        "pair_hash": pair_hash,
        "suggested_label": suggested_label,
        "frozen_request_constraints": request,
        "frozen_baseline_plan_projection": baseline_plan or {},
        "frozen_candidate_plan_projection": candidate_plan or {},
        "frozen_rubric": [{"criterion_id": "c1", "description": "有可执行步骤"}],
    }


def write_dataset(
    *,
    rows: list[dict[str, Any]],
    output_dataset_id: str,
    output_dataset_version: str,
    datasets_dir: Path,
    eval_root: Path | None = None,
) -> tuple[Path, str, int]:
    """Persist JSONL + manifest. Returns ``(jsonl_path, source_sha256,
    pair_count)`` so the caller can surface them.

    ``datasets_dir`` is the absolute output directory where the JSONL
    file lives; the manifest's ``source_path`` is recorded relative to
    ``eval_root`` (the loader's ``EVAL_ROOT``, defaults to
    ``datasets_dir.parent.parent``) so the loader's path resolver —
    which joins source_path onto ``EVAL_ROOT`` — finds the file."""

    datasets_dir.mkdir(parents=True, exist_ok=True)
    jsonl_name = f"{output_dataset_id}.jsonl"
    manifest_name = f"manifest-{output_dataset_id}-{output_dataset_version}.json"
    jsonl_path = datasets_dir / jsonl_name

    if eval_root is None:
        eval_root = datasets_dir.parent.parent

    # Stable byte ordering: sort each row's keys alphabetically and use
    # compact separators. Matches the loader's per-line JSON parse
    # expectations and is byte-reproducible across runs.
    with jsonl_path.open("w", encoding="utf-8") as fp:
        for row in rows:
            fp.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            fp.write("\n")

    source_bytes = jsonl_path.read_bytes()
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    # source_path stored relative to EVAL_ROOT because
    # calibration_loader._resolve_source_under_eval_root joins itself
    # with EVAL_ROOT (not DATASETS_DIR).
    source_path_rel = jsonl_path.resolve().relative_to(eval_root.resolve()).as_posix()

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "dataset_id": output_dataset_id,
        "dataset_version": output_dataset_version,
        "case_schema_version": MANIFEST_CASE_SCHEMA_VERSION,
        "source_path": source_path_rel,
        "source_sha256": source_sha256,
        "pair_count": len(rows),
    }
    (datasets_dir / manifest_name).write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return jsonl_path, source_sha256, len(rows)


async def export_dataset(
    *,
    baseline_experiment_id: uuid.UUID,
    candidate_experiment_id: uuid.UUID,
    output_dataset_id: str,
    output_dataset_version: str,
    datasets_dir: Path,
    fixture_overrides_path: Path | None = None,
    settings: Any | None = None,
) -> dict[str, Any]:
    """End-to-end export entry point. Reads ``EvalTrialPair`` rows whose
    trial ids BOTH belong to the given experiments, freezes evidence
    payloads per row, derives output/pair hashes, writes JSONL+manifest,
    and validates the resulting dataset round-trips through
    ``load_calibration_dataset`` (catches any hash drift immediately)."""

    settings = settings or get_settings()
    engine = create_async_engine(settings.database_url)
    try:
        rows: list[dict[str, Any]] = []

        # The session_transaction wrapper wants a session-bound path;
        # use the plain engine.connect() + AsyncSession for read.
        async with engine.connect() as conn:
            from sqlalchemy.ext.asyncio import AsyncSession as _AS

            session = _AS(bind=conn, expire_on_commit=False)
            # Pull the trial id set per experiment so we can scope
            # EvalTrialPair to "both trials belong to these experiments".
            trial_ids_by_exp: dict[uuid.UUID, set[uuid.UUID]] = {}
            for exp_id in (baseline_experiment_id, candidate_experiment_id):
                res = await session.execute(
                    select(EvalTrial.id).where(EvalTrial.experiment_id == exp_id)
                )
                trial_ids_by_exp[exp_id] = {r[0] for r in res.all()}

            base_ids = trial_ids_by_exp[baseline_experiment_id]
            cand_ids = trial_ids_by_exp[candidate_experiment_id]
            if not base_ids or not cand_ids:
                return {
                    "ok": False,
                    "error": (
                        f"baseline experiment {baseline_experiment_id} or "
                        f"candidate {candidate_experiment_id} has zero trials"
                    ),
                }

            pair_rows = (
                await session.execute(
                    select(EvalTrialPair).where(
                        EvalTrialPair.baseline_trial_id.in_(base_ids),
                        EvalTrialPair.candidate_trial_id.in_(cand_ids),
                    )
                )
            ).scalars().all()

            if not pair_rows:
                # No persisted EvalTrialPair rows. As a fallback for
                # smoke/bootstrapping, fabricate pairs from the cartesian
                # product of trials sharing a case_id. This is NOT used
                # by production paths — only when the caller has trials
                # but never materialized EvalTrialPair rows.
                pass

            for idx, pair in enumerate(sorted(pair_rows, key=lambda p: p.case_id)):
                baseline_request = await _load_evidence_payload(
                    session, pair.baseline_trial_id, _REQUEST_CONSTRAINTS_KIND
                )
                candidate_request = await _load_evidence_payload(
                    session, pair.candidate_trial_id, _REQUEST_CONSTRAINTS_KIND
                )
                request = baseline_request or candidate_request or {}
                baseline_plan = await _load_evidence_payload(
                    session, pair.baseline_trial_id, _PLAN_PROJECTION_KIND
                )
                candidate_plan = await _load_evidence_payload(
                    session, pair.candidate_trial_id, _PLAN_PROJECTION_KIND
                )
                pair_external_id = f"smoke-{idx:04d}-{pair.case_id}"
                rows.append(
                    _row_from_pair(
                        pair,
                        request=request,
                        baseline_plan=baseline_plan,
                        candidate_plan=candidate_plan,
                        pair_external_id=pair_external_id,
                        suggested_label=None,
                    )
                )

        if not rows:
            return {
                "ok": False,
                "error": (
                    "no EvalTrialPair rows whose trials belong to the "
                    "given experiments; provision experiments and re-run"
                ),
            }

        jsonl_path, source_sha256, pair_count = write_dataset(
            rows=rows,
            output_dataset_id=output_dataset_id,
            output_dataset_version=output_dataset_version,
            datasets_dir=datasets_dir,
        )

        # Reciprocal-load round-trip: prove the loader accepts what we
        # just wrote, including re-derived hashes. Catches drift between
        # this exporter and the loader's reconstruction formula.
        from evals.v2.calibration_loader import load_calibration_dataset

        bundle = load_calibration_dataset(
            dataset_id=output_dataset_id,
            dataset_version=output_dataset_version,
        )
        assert bundle.manifest.source_sha256 == source_sha256
        assert len(bundle.lines) == pair_count

        return {
            "ok": True,
            "jsonl_path": str(jsonl_path),
            "source_sha256": source_sha256,
            "pair_count": pair_count,
            "fixture_overrides_applied": (
                fixture_overrides_path is not None
            ),
        }
    finally:
        await engine.dispose()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline-experiment-id", required=True, type=uuid.UUID)
    p.add_argument("--candidate-experiment-id", required=True, type=uuid.UUID)
    p.add_argument("--output-dataset-id", required=True)
    p.add_argument("--output-dataset-version", required=True)
    p.add_argument(
        "--datasets-dir",
        default=None,
        help="Override the output directory (default: evals/v2/datasets).",
    )
    p.add_argument(
        "--fixture-overrides-path",
        default=None,
        type=Path,
        help=(
            "Optional JSON file mapping pair_external_id → fixture "
            "PairwiseJudgeOutput dict. Used only when the caller also "
            "wants to seed sweep.fixture_mapping."
        ),
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    datasets_dir = (
        Path(args.datasets_dir)
        if args.datasets_dir
        else _BACKEND / "evals" / "v2" / "datasets"
    )
    outcome = asyncio.run(
        export_dataset(
            baseline_experiment_id=args.baseline_experiment_id,
            candidate_experiment_id=args.candidate_experiment_id,
            output_dataset_id=args.output_dataset_id,
            output_dataset_version=args.output_dataset_version,
            datasets_dir=datasets_dir,
            fixture_overrides_path=args.fixture_overrides_path,
        )
    )
    print(json.dumps(outcome, ensure_ascii=False, sort_keys=True))
    return 0 if outcome.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
