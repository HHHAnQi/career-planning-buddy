"""Build the formal ``pairwise-calibration-v1`` dataset.

PR-9c.2 — formal calibration gate preparation.

Distinct from ``scripts/export_pairwise_dataset.py`` (the smoke /
candidate builder). This builder enforces the **formal v1 gate** before
writing anything:

    valid_human_pair_count >= MIN_VALID_HUMAN_PAIRS

where ``valid_human_pair_count`` = number of pairs in the sweep that
have either (a) two converging primary annotations or (b) a primary
disagreement resolved by an adjudicator. Sourced from
``eval_pairwise_human_annotations`` (DB) — never from the JSONL.

When the gate is unmet (today: 0 / 100), the builder:

* in default mode → raises ``CalibrationGateUnmet`` and writes NOTHING
  to ``pairwise-calibration-v1.jsonl``;
* in ``--dry-run`` mode → returns a JSON status report and writes
  nothing.

When the gate IS met, the builder additionally enforces 5 per-pair
invariants the smoke exporter does NOT check:

1. **Missing evidence** — any ``frozen_*`` projection absent ⇒
   ``MissingProjectionError``.
2. **Identical baseline vs candidate projection** (degenerate pair) ⇒
   ``DegeneratePairError``.
3. **Duplicate ``pair_hash``** across rows ⇒
   ``DuplicatePairHashError``.
4. **``output_hash`` recomputation drift** ⇒ ``HashDriftError``.
5. **``pair_hash`` recomputation drift** ⇒ ``HashDriftError``.

Plus an explicit forbidden-field scan (belt-and-suspenders on top of
``CalibrationExportLine``'s ``StrictModel(extra="forbid")``): any row
carrying ``reviewer_id`` / ``winner`` / ``human_label`` /
``adjudication*`` / etc. ⇒ ``ForbiddenFieldError``.

Output (only on gate pass)::

    evals/v2/datasets/pairwise-calibration-v1.jsonl
    evals/v2/datasets/manifest-pairwise-calibration-v1-v1.json

The output is byte-idempotent: a re-run with the same DB state produces
byte-identical files (deterministic ordering, no timestamps, no run-id).
The builder verifies this implicitly via the loader round-trip.

DO NOT ALTER the calibration formula, thresholds, or schema. This script
is a gate-enforcing wrapper over the existing export contract.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    create_async_engine,
)

from app.core.config import get_settings  # noqa: E402
from app.models.eval import (  # noqa: E402
    EvalPairwiseHumanAnnotation,
    EvalTrial,
    EvalTrialPair,
)
from evals.v2.contracts import canonical_sha256  # noqa: E402
from scripts.export_pairwise_dataset import (  # noqa: E402
    _PLAN_PROJECTION_KIND,
    _REQUEST_CONSTRAINTS_KIND,
    _load_evidence_payload,
    _row_from_pair,
    write_dataset,
)

# --- formal v1 contract ----------------------------------------------------

DATASET_ID = "pairwise-calibration-v1"
DATASET_VERSION = "v1"

#: Minimum number of valid human-annotated primary pairs required before
#: this builder will emit the formal v1 dataset. Per handoff §8.8.
MIN_VALID_HUMAN_PAIRS = 100

#: Fields a v1 row must NEVER carry. The loader's ``StrictModel`` already
#: rejects unknown fields, but we scan explicitly so the failure names
#: the offending field and is impossible to silently regress.
FORBIDDEN_FIELDS = frozenset({
    "reviewer_id",
    "winner",
    "human_label",
    "label",
    "annotation",
    "adjudication",
    "adjudication_result",
    "adjudicator",
    "judge_label",
    "score",
    "rating",
})


# --- named exceptions ------------------------------------------------------


class CalibrationGateUnmet(Exception):
    """Raised when the formal v1 gate is not satisfied (default mode).

    Carries a status dict so callers can surface the gap to operators
    without re-querying.
    """

    def __init__(self, *, valid: int, required: int) -> None:
        self.valid = valid
        self.required = required
        super().__init__(
            f"formal v1 gate unmet: valid_human_pair_count={valid} "
            f"< required={required}"
        )


class MissingProjectionError(Exception):
    """A pair is missing one or more frozen projections."""

    def __init__(self, *, pair_id: uuid.UUID, kinds: list[str]) -> None:
        self.pair_id = pair_id
        self.kinds = kinds
        super().__init__(
            f"pair {pair_id} missing evidence projections: {sorted(kinds)}"
        )


class DegeneratePairError(Exception):
    """Baseline and candidate projections are byte-identical — no signal."""

    def __init__(self, *, pair_id: uuid.UUID, case_id: str) -> None:
        self.pair_id = pair_id
        self.case_id = case_id
        super().__init__(
            f"pair {pair_id} (case {case_id}) is degenerate: baseline and "
            "candidate plan projections hash identically — no comparison signal"
        )


class DuplicatePairHashError(Exception):
    """Two rows produced the same ``pair_hash``."""

    def __init__(self, *, pair_hash: str) -> None:
        self.pair_hash = pair_hash
        super().__init__(f"duplicate pair_hash across rows: {pair_hash}")


class HashDriftError(Exception):
    """Recomputed hash does not match the stored hash on a row."""

    def __init__(
        self, *, pair_id: uuid.UUID, field: str, stored: str, recomputed: str
    ) -> None:
        self.pair_id = pair_id
        self.field = field
        self.stored = stored
        self.recomputed = recomputed
        super().__init__(
            f"pair {pair_id} {field} drift: stored={stored} recomputed={recomputed}"
        )


class ForbiddenFieldError(Exception):
    """An emitted row carries a human-label/adjudication field."""

    def __init__(self, *, pair_id: uuid.UUID, field: str) -> None:
        self.pair_id = pair_id
        self.field = field
        super().__init__(
            f"pair {pair_id} row carries forbidden field {field!r} "
            f"(v1 JSONL must not carry human labels)"
        )


# --- gate check ------------------------------------------------------------


async def _count_valid_human_pairs(
    session: AsyncSession, *, pair_ids: list[uuid.UUID]
) -> int:
    """Count pairs with valid primary consensus OR primary+adjudication.

    A pair is "valid" when:
      * it has ≥2 primary annotations AND those primaries converge on a
        single ``normalized_winner``, OR
      * it has ≥2 primary annotations that disagree AND an adjudicator
        annotation exists.

    The primaries need not come from distinct reviewer_ids for this
    count; the unique-pair reviewer uniqueness is enforced elsewhere
    (``uq_eval_pairwise_ann_dataset_pair_reviewer_surface``). We count
    pair-level validity, not reviewer identity.
    """

    if not pair_ids:
        return 0

    # Pull every annotation on the candidate pairs, primary-only first.
    ann_rows = (
        await session.execute(
            select(
                EvalPairwiseHumanAnnotation.pair_id,
                EvalPairwiseHumanAnnotation.normalized_winner,
                EvalPairwiseHumanAnnotation.is_adjudication,
            ).where(EvalPairwiseHumanAnnotation.pair_id.in_(pair_ids))
        )
    ).all()

    primaries_by_pair: dict[uuid.UUID, list[str]] = {}
    adjudication_pairs: set[uuid.UUID] = set()
    for pid, winner, is_adj in ann_rows:
        pid = uuid.UUID(str(pid))  # normalize asyncpg.UUID
        if is_adj:
            adjudication_pairs.add(pid)
        else:
            primaries_by_pair.setdefault(pid, []).append(str(winner))

    valid = 0
    for pid, winners in primaries_by_pair.items():
        if len(winners) < 2:
            continue
        unique = set(winners)
        if len(unique) == 1:
            # consensus
            valid += 1
        elif pid in adjudication_pairs:
            # disagreement resolved
            valid += 1
    return valid


# --- per-pair validation ---------------------------------------------------


def _validate_row(row: dict[str, Any], *, pair: EvalTrialPair) -> None:
    """Run all 5 per-row + cross-row checks. Raises named exceptions."""

    pair_id = uuid.UUID(str(pair.id))

    # 1. forbidden fields (explicit)
    for key in row:
        if key in FORBIDDEN_FIELDS:
            raise ForbiddenFieldError(pair_id=pair_id, field=key)

    # 2. missing projections
    missing = [
        kind
        for kind, payload in (
            ("frozen_request_constraints", row.get("frozen_request_constraints")),
            ("frozen_baseline_plan_projection", row.get("frozen_baseline_plan_projection")),
            ("frozen_candidate_plan_projection", row.get("frozen_candidate_plan_projection")),
        )
        if payload is None
    ]
    if missing:
        raise MissingProjectionError(pair_id=pair_id, kinds=missing)

    # 3. degenerate pair — baseline vs candidate byte-identical
    baseline_proj = row["frozen_baseline_plan_projection"]
    candidate_proj = row["frozen_candidate_plan_projection"]
    if canonical_sha256(baseline_proj) == canonical_sha256(candidate_proj):
        raise DegeneratePairError(pair_id=pair_id, case_id=pair.case_id)

    # 4. output_hash drift (baseline + candidate)
    request = row["frozen_request_constraints"]
    recomputed_b = canonical_sha256({"request": request, "plan": baseline_proj})
    if recomputed_b != row["baseline_output_hash"]:
        raise HashDriftError(
            pair_id=pair_id,
            field="baseline_output_hash",
            stored=row["baseline_output_hash"],
            recomputed=recomputed_b,
        )
    recomputed_c = canonical_sha256({"request": request, "plan": candidate_proj})
    if recomputed_c != row["candidate_output_hash"]:
        raise HashDriftError(
            pair_id=pair_id,
            field="candidate_output_hash",
            stored=row["candidate_output_hash"],
            recomputed=recomputed_c,
        )

    # 5. pair_hash drift
    recomputed_pair = canonical_sha256(
        {
            "schema_version": "eval-trial-pair/v1",
            "case_id": pair.case_id,
            "baseline_trial_id": str(pair.baseline_trial_id),
            "candidate_trial_id": str(pair.candidate_trial_id),
            "baseline_output_hash": row["baseline_output_hash"],
            "candidate_output_hash": row["candidate_output_hash"],
        }
    )
    if recomputed_pair != row["pair_hash"]:
        raise HashDriftError(
            pair_id=pair_id,
            field="pair_hash",
            stored=row["pair_hash"],
            recomputed=recomputed_pair,
        )


def _check_duplicate_pair_hashes(rows: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for row in rows:
        ph = row["pair_hash"]
        if ph in seen:
            raise DuplicatePairHashError(pair_hash=ph)
        seen.add(ph)


# --- main entry ------------------------------------------------------------


async def build_v1(
    *,
    baseline_experiment_id: uuid.UUID,
    candidate_experiment_id: uuid.UUID,
    datasets_dir: Path,
    dry_run: bool = False,
    settings: Any | None = None,
) -> dict[str, Any]:
    """Build (or dry-run) the formal v1 dataset.

    Returns a JSON-serializable status report. On a non-dry-run with an
    unmet gate, raises ``CalibrationGateUnmet``.
    """

    settings = settings or get_settings()
    engine = create_async_engine(settings.database_url)

    try:
        async with engine.connect() as conn:
            from sqlalchemy.ext.asyncio import AsyncSession as _AS

            session = _AS(bind=conn, expire_on_commit=False)

            # --- resolve trial-pair rows ---------------------------------
            base_ids = {
                uuid.UUID(str(r[0]))
                for r in (
                    await session.execute(
                        select(EvalTrial.id).where(
                            EvalTrial.experiment_id == baseline_experiment_id
                        )
                    )
                ).all()
            }
            cand_ids = {
                uuid.UUID(str(r[0]))
                for r in (
                    await session.execute(
                        select(EvalTrial.id).where(
                            EvalTrial.experiment_id == candidate_experiment_id
                        )
                    )
                ).all()
            }
            if not base_ids or not cand_ids:
                return {
                    "ok": False,
                    "reason": "empty_experiment",
                    "baseline_trial_count": len(base_ids),
                    "candidate_trial_count": len(cand_ids),
                }

            pair_rows = (
                await session.execute(
                    select(EvalTrialPair)
                    .where(
                        EvalTrialPair.baseline_trial_id.in_(base_ids),
                        EvalTrialPair.candidate_trial_id.in_(cand_ids),
                    )
                    .order_by(EvalTrialPair.case_id, EvalTrialPair.created_at)
                )
            ).scalars().all()
            if not pair_rows:
                return {
                    "ok": False,
                    "reason": "no_trial_pairs",
                    "baseline_experiment_id": str(baseline_experiment_id),
                    "candidate_experiment_id": str(candidate_experiment_id),
                }

            # De-duplicate on (case_id, baseline_trial_id,
            # candidate_trial_id). The ``eval_trial_pairs`` UNIQUE
            # constraint in some environments covers (dataset, case,
            # trial) but older dev-DB state can still carry duplicate
            # pair *rows* for the same trial pair (observed: 46 rows
            # for 23 unique trial pairs after a re-provision). Keep the
            # earliest by created_at so the output is deterministic.
            seen_pairs: set[tuple[str, uuid.UUID, uuid.UUID]] = set()
            deduped: list[EvalTrialPair] = []
            for p in pair_rows:
                key = (
                    p.case_id,
                    uuid.UUID(str(p.baseline_trial_id)),
                    uuid.UUID(str(p.candidate_trial_id)),
                )
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                deduped.append(p)
            pair_rows = deduped

            # --- gate check (DB-sourced human annotations) --------------
            pair_ids = [uuid.UUID(str(p.id)) for p in pair_rows]
            valid_human = await _count_valid_human_pairs(
                session, pair_ids=pair_ids
            )

            if valid_human < MIN_VALID_HUMAN_PAIRS:
                report = {
                    "ok": False,
                    "reason": "gate_unmet",
                    "valid_human_pair_count": valid_human,
                    "required": MIN_VALID_HUMAN_PAIRS,
                    "candidate_pair_count": len(pair_rows),
                    "dataset_id": DATASET_ID,
                    "dataset_version": DATASET_VERSION,
                }
                if dry_run:
                    return report
                raise CalibrationGateUnmet(
                    valid=valid_human, required=MIN_VALID_HUMAN_PAIRS
                )

            # --- build + validate each row ------------------------------
            rows: list[dict[str, Any]] = []
            for idx, pair in enumerate(pair_rows):
                baseline_request = await _load_evidence_payload(
                    session, pair.baseline_trial_id, _REQUEST_CONSTRAINTS_KIND
                )
                candidate_request = await _load_evidence_payload(
                    session, pair.candidate_trial_id, _REQUEST_CONSTRAINTS_KIND
                )
                request = baseline_request or candidate_request
                baseline_plan = await _load_evidence_payload(
                    session, pair.baseline_trial_id, _PLAN_PROJECTION_KIND
                )
                candidate_plan = await _load_evidence_payload(
                    session, pair.candidate_trial_id, _PLAN_PROJECTION_KIND
                )
                row = _row_from_pair(
                    pair,
                    request=request or {},
                    baseline_plan=baseline_plan,
                    candidate_plan=candidate_plan,
                    pair_external_id=f"v1-{idx:04d}-{pair.case_id}",
                    suggested_label=None,
                )
                # The smoke exporter silently substitutes {} for missing
                # projections; the formal builder must reject explicitly.
                # Re-instate None so the validator can flag them.
                if baseline_request is None and candidate_request is None:
                    row["frozen_request_constraints"] = None
                if baseline_plan is None:
                    row["frozen_baseline_plan_projection"] = None
                if candidate_plan is None:
                    row["frozen_candidate_plan_projection"] = None
                _validate_row(row, pair=pair)
                rows.append(row)

            _check_duplicate_pair_hashes(rows)

        # --- write + round-trip ----------------------------------------
        # eval_root must equal the loader's EVAL_ROOT so the manifest's
        # source_path (stored relative to eval_root) resolves correctly
        # when ``load_calibration_dataset`` re-reads it. The loader's
        # default EVAL_ROOT = DATASETS_DIR.parent, so we match it here.
        jsonl_path, source_sha256, pair_count = write_dataset(
            rows=rows,
            output_dataset_id=DATASET_ID,
            output_dataset_version=DATASET_VERSION,
            datasets_dir=datasets_dir,
            eval_root=datasets_dir.parent,
        )

        from evals.v2.calibration_loader import load_calibration_dataset

        bundle = load_calibration_dataset(
            dataset_id=DATASET_ID, dataset_version=DATASET_VERSION
        )
        assert bundle.manifest.source_sha256 == source_sha256
        assert len(bundle.lines) == pair_count

        return {
            "ok": True,
            "jsonl_path": str(jsonl_path),
            "source_sha256": source_sha256,
            "pair_count": pair_count,
            "valid_human_pair_count": valid_human,
            "dataset_id": DATASET_ID,
            "dataset_version": DATASET_VERSION,
        }
    finally:
        await engine.dispose()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--baseline-experiment-id", required=True, type=uuid.UUID
    )
    p.add_argument(
        "--candidate-experiment-id", required=True, type=uuid.UUID
    )
    p.add_argument(
        "--datasets-dir",
        default=None,
        help="Override the output directory (default: evals/v2/datasets).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Report-only. Prints the gate status and exits 0 even when "
            "the gate is unmet; writes nothing."
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
    try:
        outcome = asyncio.run(
            build_v1(
                baseline_experiment_id=args.baseline_experiment_id,
                candidate_experiment_id=args.candidate_experiment_id,
                datasets_dir=datasets_dir,
                dry_run=args.dry_run,
            )
        )
    except CalibrationGateUnmet as exc:
        print(json.dumps(
            {
                "ok": False,
                "reason": "gate_unmet",
                "valid_human_pair_count": exc.valid,
                "required": exc.required,
                "dataset_id": DATASET_ID,
                "dataset_version": DATASET_VERSION,
                "message": (
                    "no pairwise-calibration-v1.jsonl was written — the "
                    "formal v1 gate requires >=100 valid human primary "
                    "pairs (consensus or adjudication-resolved). See "
                    "evals/v2/datasets/REVIEWER_WORKFLOW.md."
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
        ))
        return 2
    print(json.dumps(outcome, ensure_ascii=False, sort_keys=True))
    return 0 if outcome.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
