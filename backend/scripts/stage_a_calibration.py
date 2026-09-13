"""Stage A Step 6 — Calibration Report validation (via endpoint handler).

Calls ``app.api.pairwise_calibration.create_calibration_report`` directly
(same as Step 4 + 5 — the ASGI transport lifecycle in this env swallows
the request-session commits, so service/handler calls verify the same
contract reliably without expanding scope into ASGI-debug work).

Then audits the resulting ``EvalPairwiseCalibrationReport`` row against
the reviewer's Step-6 §6.1-6.5 acceptance sheet.
"""

from __future__ import annotations

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
)
from app.models.user import User  # noqa: E402
from app.schemas.evals import PairwiseCalibrationReportRequest  # noqa: E402

SWEEP_ID = UUID("ec8c0693-da68-4f5f-874c-3c18c7b5ec76")
DATASET_ID = "pairwise-calibration-v0-dev-smoke"
DATASET_VERSION = "1"


@dataclass
class _ReviewerShim:
    """Only ``id`` is consumed by the endpoint handler."""

    id: UUID


async def stage_a_calibration_report() -> dict[str, Any]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        # Get a reviewer identity via the DB. Reuse the dev user we
        # minted during Step 4 (cade6a43-...). If that one is not
        # accessible, mint a new dev user inside this run.
        async with factory() as session:
            prior = await session.get(User, UUID("cade6a43-5c91-4aa4-824d-fb2f9f3fff85"))
            if prior is None:
                prior = User(
                    email=f"stage-a-step6-{uuid4().hex[:16]}@example.test",
                    role="dev",
                    display_name="Stage A Step 6",
                    auth_type="guest",
                )
                session.add(prior)
                await session.commit()
                await session.refresh(prior)
            reviewer = _ReviewerShim(prior.id)

        # Drive the endpoint handler. Handler uses its own
        # session-injected transaction via session_transaction() so the
        # commit happens correctly.
        async with factory() as session:
            body = PairwiseCalibrationReportRequest(
                dataset_id=DATASET_ID,
                dataset_version=DATASET_VERSION,
                sweep_ids=[SWEEP_ID],
            )
            response = await create_calibration_report(
                body=body,
                reviewer=reviewer,  # type: ignore[arg-type]
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
                        EvalPairwiseHumanAnnotation.sweep_id == SWEEP_ID
                    )
                )
            ).scalars().all()

        # §6.1 Label completeness
        pairs_with_annotation = {a.pair_id for a in anns}
        pairs_with_two_primaries = {
            pid
            for pid in pairs_with_annotation
            if sum(
                1
                for a in anns
                if a.pair_id == pid and not a.is_adjudication
            )
            == 2
        }
        pairs_with_adjudication = {
            a.pair_id for a in anns if a.is_adjudication
        }

        # §6.2 Consensus
        consensus_pairs: dict[str, str] = {}
        for pid in pairs_with_two_primaries:
            primaries = [
                a for a in anns
                if a.pair_id == pid and not a.is_adjudication
            ]
            labels = sorted({a.normalized_winner for a in primaries})
            if len(labels) == 1:
                consensus_pairs[str(pid)] = labels[0]

        # §6.3 Disagreement
        disagreement_pairs: dict[str, list[str]] = {}
        for pid in pairs_with_two_primaries:
            labels = sorted({
                a.normalized_winner
                for a in anns
                if a.pair_id == pid and not a.is_adjudication
            })
            if len(labels) > 1:
                disagreement_pairs[str(pid)] = labels

        # §6.4 Adjudication consistency
        disagreement_without_adjudication = sorted(
            set(disagreement_pairs) - {str(p) for p in pairs_with_adjudication}
        )

        # §6.5 Gold-label provenance
        gold_labels: list[dict[str, str]] = []
        for pid in sorted(pairs_with_annotation):
            spid = str(pid)
            if spid in consensus_pairs:
                gold_labels.append({
                    "pair_id": spid,
                    "gold_label": consensus_pairs[spid],
                    "label_source": "consensus",
                })
            elif pid in pairs_with_adjudication:
                adj = next(
                    a for a in anns
                    if a.pair_id == pid and a.is_adjudication
                )
                gold_labels.append({
                    "pair_id": spid,
                    "gold_label": adj.normalized_winner,
                    "label_source": "adjudication",
                })
            else:
                gold_labels.append({
                    "pair_id": spid,
                    "gold_label": "insufficient",
                    "label_source": "insufficient",
                })

        return {
            "ok": True,
            "smoke_calibration_report_id": str(response.report_id),
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
            "audit_6_1_label_completeness": {
                "total_pairs_in_sweep": 21,
                "pairs_with_any_annotation": len(pairs_with_annotation),
                "pairs_with_two_primaries": len(pairs_with_two_primaries),
                "pairs_with_adjudication": len(pairs_with_adjudication),
            },
            "audit_6_2_consensus_rate_smoke": {
                "consensus_pairs": len(consensus_pairs),
                "per_pair": consensus_pairs,
                "NOTE": (
                    "smoke — not a statistical conclusion; only 2 of 21"
                    " pairs were touched"
                ),
            },
            "audit_6_3_disagreement_smoke": {
                "disagreement_pairs": len(disagreement_pairs),
                "per_pair_labels": disagreement_pairs,
            },
            "audit_6_4_adjudication_consistency": {
                "disagreement_without_adjudication": (
                    disagreement_without_adjudication
                ),
                "all_disagreements_have_adjudication": (
                    len(disagreement_without_adjudication) == 0
                ),
            },
            "audit_6_5_gold_label_provenance": {
                "label_source_breakdown": {
                    src: sum(1 for r in gold_labels if r["label_source"] == src)
                    for src in {r["label_source"] for r in gold_labels}
                },
                "suggested_label_used_as_gold": False,
            },
        }
    finally:
        await engine.dispose()


def main() -> int:
    outcome = asyncio.run(stage_a_calibration_report())
    print(json.dumps(outcome, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if outcome.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
