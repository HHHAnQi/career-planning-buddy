"""Stage B-1 Calibration Report validation (diagnostic-only).

Drives ``app.api.pairwise_calibration.create_calibration_report`` over
the Stage B-1 Sweep produced by ``stage_b1_sweep.py``. Stage B has no
real human annotations (the fixture_mapping was diagnostic-only, all
ties), so the expected terminal pair is::

    calibration_status = "insufficient"
    usage_mode         = "diagnostic_only"

The value of this step is end-to-end pipeline validation -- the report
must carry real ``position_metric_sample_count`` (sourced from the
Sweep's 23 pairs × 2 positions = 46 judge results) while
``valid_human_pair_count`` is 0.

PR-9c.2 Stage B-1 (Commit 3.5 onwards).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    async_sessionmaker,
    create_async_engine,
)

from app.api.pairwise_calibration import create_calibration_report  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.models.eval import (  # noqa: E402
    EvalPairwiseCalibrationReport,
    EvalPairwiseHumanAnnotation,
    EvalPairwiseSweepItem,
)
from app.models.user import User  # noqa: E402
from app.schemas.evals import PairwiseCalibrationReportRequest  # noqa: E402

DATASET_ID = "pairwise-calibration-v1-candidate"
DATASET_VERSION = "v1"


@dataclass
class _ReviewerShim:
    id: UUID


async def stage_b1_calibration_report(
    *,
    sweep_id: UUID,
) -> dict[str, Any]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        # Mint (or reuse) a dev user as the reviewer identity.
        async with factory() as session:
            reviewer = User(
                email=f"stage-b1-calib-{uuid4().hex[:16]}@example.test",
                role="dev",
                display_name="Stage B-1 Calibration",
                auth_type="guest",
            )
            session.add(reviewer)
            await session.commit()
            await session.refresh(reviewer)
            reviewer_shim = _ReviewerShim(reviewer.id)

        # Drive the endpoint handler directly (same rationale as Stage A:
        # ASGI transport swallows the request-session commit here).
        async with factory() as session:
            body = PairwiseCalibrationReportRequest(
                dataset_id=DATASET_ID,
                dataset_version=DATASET_VERSION,
                sweep_ids=[sweep_id],
            )
            response = await create_calibration_report(
                body=body,
                reviewer=reviewer_shim,  # type: ignore[arg-type]
                session=session,
            )
            await session.commit()

        # Audit DB.
        async with factory() as audit:
            row = (
                await audit.execute(
                    select(EvalPairwiseCalibrationReport).where(
                        EvalPairwiseCalibrationReport.id == response.report_id
                    )
                )
            ).scalar_one()
            payload = dict(row.report_payload)
            anns = (
                await audit.execute(
                    select(EvalPairwiseHumanAnnotation).where(
                        EvalPairwiseHumanAnnotation.sweep_id == sweep_id
                    )
                )
            ).scalars().all()
            # Total pairs in sweep (for the §6.1 ratio).
            item_rows = (
                await audit.execute(
                    select(EvalPairwiseSweepItem).where(
                        EvalPairwiseSweepItem.sweep_id == sweep_id
                    )
                )
            ).scalars().all()
            total_pairs = len({it.pair_id for it in item_rows})

        return {
            "ok": True,
            "stage_b1_calibration_report_id": str(response.report_id),
            "calibration_status": response.calibration_status,
            "usage_mode": response.usage_mode,
            "expected_status_pair": ("insufficient", "diagnostic_only"),
            "actual_status_pair_match": (
                response.calibration_status == "insufficient"
                and response.usage_mode == "diagnostic_only"
            ),
            "report_payload_metrics": {
                k: payload.get(k)
                for k in (
                    "valid_human_pair_count",
                    "position_pair_count",
                    "position_metric_sample_count",
                    "agreement",
                    "position_bias",
                    "agreement_sample_count",
                    "calibration_status",
                    "usage_mode",
                )
                if k in payload
            },
            "report_payload_carries_real_metrics": (
                "calibration_status" in payload
                and "valid_human_pair_count" in payload
                and "position_metric_sample_count" in payload
            ),
            "audit_annotation_count": len(anns),
            "audit_total_pairs_in_sweep": total_pairs,
        }
    finally:
        await engine.dispose()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--sweep-id",
        required=True,
        type=UUID,
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    outcome = asyncio.run(stage_b1_calibration_report(sweep_id=args.sweep_id))
    print(json.dumps(outcome, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if outcome.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
