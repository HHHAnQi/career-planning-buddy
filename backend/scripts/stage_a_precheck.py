"""Stage A pair-smoke precheck.

Determines whether a (baseline_experiment_id, candidate_experiment_id)
pair is ready to drive the ``pairwise-calibration-v0-dev-smoke``
pipeline end-to-end. Reads only from the database; never mutates.

REQUIRED for proceed-beyond-precheck (per Stage A E′ acceptance):

    eligible_pair_count                >= 20
    nonidentical_projection_pair_count >= 20
    comparison_signal_rate             == 1.0
    exit_code                          == 0

If any guard fails, exit nonzero and emit the JSON evidence block so a
human can read off exactly which case is degenerate.

Definitions (matching the loader's hash formula):

* A pair is ELIGIBLE for smoke if both its trials are
  ``status='completed'`` AND each has BOTH ``request_constraints`` and
  ``plan_projection`` evidence rows. Loader's per-line hash requires
  both projections to exist (otherwise ``frozen_*_plan_projection`` is
  empty and ``output_hash`` is meaningless for that pair).
* A pair is IDENTICAL if the two ``PLAN_PROJECTION.projection_json``
  blobs canonicalize to the same bytes (loader hash will collide).
* A pair is NONIDENTICAL otherwise.
* ``comparison_signal_rate`` is ``nonidentical / eligible`` — every
  eligible pair MUST carry real baseline↔candidate divergence (1.0).

Inputs are pure-CLI (no hardcoded UUID). Output is JSON to stdout.
Exit codes:
  0  all guards OK
  2  zero completed experiments (ZERODATA)
  3  eligible < 20
  4  eligible >= 20 but nonidentical < 20 (signal collapse)
  5  comparison_signal_rate < 1.0

PR-9c.2 Commit 3.4 (Stage A E′).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    create_async_engine,
)

from app.core.config import get_settings  # noqa: E402
from app.models.eval import EvalEvidenceItem, EvalTrial, EvalTrialPair  # noqa: E402
from evals.v2.contracts import canonical_sha256  # noqa: E402

_REQUEST_CONSTRAINTS_KIND = "request_constraints"
_PLAN_PROJECTION_KIND = "plan_projection"
_MIN_ELIGIBLE = 20


async def _trial_completed(session: AsyncSession, trial_id: UUID) -> bool:
    row = (
        await session.execute(
            select(EvalTrial.status).where(EvalTrial.id == trial_id)
        )
    ).scalar_one_or_none()
    return row == "completed"


async def _evidence_payload(
    session: AsyncSession, trial_id: UUID, kind: str
) -> dict[str, Any] | None:
    rows = (
        await session.execute(
            select(EvalEvidenceItem.projection_json)
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
    payload = rows[0]
    return payload if isinstance(payload, dict) else None


async def _pair_signal(
    session: AsyncSession, pair: EvalTrialPair
) -> dict[str, Any]:
    """Compute one pair's eligibility + identity signal."""

    b_completed = await _trial_completed(
        session, pair.baseline_trial_id
    )
    c_completed = await _trial_completed(
        session, pair.candidate_trial_id
    )
    b_req = await _evidence_payload(
        session, pair.baseline_trial_id, _REQUEST_CONSTRAINTS_KIND
    )
    b_plan = await _evidence_payload(
        session, pair.baseline_trial_id, _PLAN_PROJECTION_KIND
    )
    c_req = await _evidence_payload(
        session, pair.candidate_trial_id, _REQUEST_CONSTRAINTS_KIND
    )
    c_plan = await _evidence_payload(
        session, pair.candidate_trial_id, _PLAN_PROJECTION_KIND
    )
    eligible = (
        b_completed
        and c_completed
        and b_req is not None
        and b_plan is not None
        and c_req is not None
        and c_plan is not None
    )
    identical = (
        eligible
        and canonical_sha256(b_plan or {}) == canonical_sha256(c_plan or {})
    )
    return {
        "pair_id": str(pair.id),
        "case_id": pair.case_id,
        "eligible": eligible,
        "identical": identical,
        "nonidentical": eligible and not identical,
        "baseline_trial_id": str(pair.baseline_trial_id),
        "candidate_trial_id": str(pair.candidate_trial_id),
    }


async def precheck(
    *,
    baseline_experiment_id: UUID,
    candidate_experiment_id: UUID,
    settings: Any | None = None,
    session: AsyncSession | None = None,
) -> dict[str, Any]:
    """Compute the metrics block. If ``session`` is supplied, run inside
    that session (test path); otherwise open a fresh read-only engine.

    Returns the evidence dict; caller decides the exit code."""

    if session is not None:
        return await _precheck_in_session(
            session=session,
            baseline_experiment_id=baseline_experiment_id,
            candidate_experiment_id=candidate_experiment_id,
        )
    settings = settings or get_settings()
    engine = create_async_engine(settings.database_url)
    try:
        async with AsyncSession(bind=engine, expire_on_commit=False) as s:
            return await _precheck_in_session(
                session=s,
                baseline_experiment_id=baseline_experiment_id,
                candidate_experiment_id=candidate_experiment_id,
            )
    finally:
        await engine.dispose()


async def _precheck_in_session(
    *,
    session: AsyncSession,
    baseline_experiment_id: UUID,
    candidate_experiment_id: UUID,
) -> dict[str, Any]:
    # Verify both experiments exist and are completed.
    base_status = (
        await session.execute(
            select(EvalTrial.id)
            .where(EvalTrial.experiment_id == baseline_experiment_id)
            .limit(1)
        )
    ).first()
    cand_status = (
        await session.execute(
            select(EvalTrial.id)
            .where(EvalTrial.experiment_id == candidate_experiment_id)
            .limit(1)
        )
    ).first()
    if base_status is None or cand_status is None:
        return {
            "ok": False,
            "reason": "ZERODATA: one or both experiments have no trials",
            "baseline_experiment_id": str(baseline_experiment_id),
            "candidate_experiment_id": str(candidate_experiment_id),
            "metrics": {
                "eligible_pair_count": 0,
                "nonidentical_projection_pair_count": 0,
                "identical_projection_pair_count": 0,
                "comparison_signal_rate": 0.0,
            },
            "pairs": [],
        }

    # Discover all baseline/candidate trial ids.
    base_trials = {
        row[0]
        for row in (
            await session.execute(
                select(EvalTrial.id).where(
                    EvalTrial.experiment_id == baseline_experiment_id
                )
            )
        ).all()
    }
    cand_trials = {
        row[0]
        for row in (
            await session.execute(
                select(EvalTrial.id).where(
                    EvalTrial.experiment_id == candidate_experiment_id
                )
            )
        ).all()
    }
    if not base_trials or not cand_trials:
        return {
            "ok": False,
            "reason": "ZERODATA: baseline or candidate has zero trials",
            "baseline_experiment_id": str(baseline_experiment_id),
            "candidate_experiment_id": str(candidate_experiment_id),
            "metrics": {
                "eligible_pair_count": 0,
                "nonidentical_projection_pair_count": 0,
                "identical_projection_pair_count": 0,
                "comparison_signal_rate": 0.0,
            },
            "pairs": [],
        }

    pair_rows = (
        await session.execute(
            select(EvalTrialPair)
            .where(
                EvalTrialPair.baseline_trial_id.in_(base_trials),
                EvalTrialPair.candidate_trial_id.in_(cand_trials),
            )
            .order_by(EvalTrialPair.case_id)
        )
    ).scalars().all()

    pair_signals = [await _pair_signal(session, pair) for pair in pair_rows]
    eligible = [s for s in pair_signals if s["eligible"]]
    identical = [s for s in eligible if s["identical"]]
    nonidentical = [s for s in eligible if s["nonidentical"]]
    eligible_count = len(eligible)
    nonidentical_count = len(nonidentical)
    identical_count = len(identical)
    rate = (
        nonidentical_count / eligible_count if eligible_count else 0.0
    )
    ok = (
        eligible_count >= _MIN_ELIGIBLE
        and nonidentical_count >= _MIN_ELIGIBLE
        and rate == 1.0
    )
    return {
        "ok": ok,
        "baseline_experiment_id": str(baseline_experiment_id),
        "candidate_experiment_id": str(candidate_experiment_id),
        "metrics": {
            "eligible_pair_count": eligible_count,
            "nonidentical_projection_pair_count": nonidentical_count,
            "identical_projection_pair_count": identical_count,
            "comparison_signal_rate": rate,
            "min_required": _MIN_ELIGIBLE,
        },
        "pairs": pair_signals,
    }


def _exit_code(result: dict[str, Any]) -> int:
    if result.get("reason", "").startswith("ZERODATA"):
        return 2
    metrics = result["metrics"]
    if metrics["eligible_pair_count"] < _MIN_ELIGIBLE:
        return 3
    if metrics["nonidentical_projection_pair_count"] < _MIN_ELIGIBLE:
        return 4
    if metrics["comparison_signal_rate"] < 1.0:
        return 5
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--baseline-experiment-id",
        required=True,
        type=UUID,
    )
    p.add_argument(
        "--candidate-experiment-id",
        required=True,
        type=UUID,
    )
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to write the JSON evidence block.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    result = asyncio.run(
        precheck(
            baseline_experiment_id=args.baseline_experiment_id,
            candidate_experiment_id=args.candidate_experiment_id,
        )
    )
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True)
    print(payload)
    if args.output is not None:
        args.output.write_text(payload + "\n", encoding="utf-8")
    return _exit_code(result)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
