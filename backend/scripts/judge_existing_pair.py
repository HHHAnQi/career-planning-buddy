"""Run position-balanced live Judge calls for persisted completed Trials."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.repositories.evals import EvalRepository  # noqa: E402
from app.services.evals import EvalService  # noqa: E402
from evals.v2.judge_factory import build_pairwise_judge  # noqa: E402
from evals.v2.pairwise import PositionVariant  # noqa: E402


async def judge_existing(
    *, baseline_experiment_id: UUID, candidate_experiment_id: UUID, case_id: str
) -> dict[str, object]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    comparison_group = (
        f"b0-b3-live-smoke:{baseline_experiment_id}:{candidate_experiment_id}"
    )
    try:
        async with factory() as session:
            repo = EvalRepository(session)
            baseline = {
                trial.case_id: trial
                for trial in await repo.list_trials(baseline_experiment_id)
            }.get(case_id)
            candidate = {
                trial.case_id: trial
                for trial in await repo.list_trials(candidate_experiment_id)
            }.get(case_id)
        if baseline is None or candidate is None:
            raise RuntimeError("case is missing from one experiment")
        if baseline.status != "completed" or candidate.status != "completed":
            raise RuntimeError("both trials must be completed")

        judge = build_pairwise_judge(settings)
        results: list[dict[str, object]] = []
        async with factory() as session:
            service = EvalService(session)
            for position in (PositionVariant.BASELINE, PositionVariant.SWAPPED):
                judge_run_id = uuid5(
                    NAMESPACE_URL,
                    f"{comparison_group}:{case_id}:{position.value}",
                )
                pair, result = await service.run_pairwise_judge(
                    baseline_trial_id=baseline.id,
                    candidate_trial_id=candidate.id,
                    case_id=case_id,
                    comparison_group_id=comparison_group,
                    judge_run_id=judge_run_id,
                    judge=judge,
                    position_variant=position,
                )
                results.append(
                    {
                        "pair_id": pair.id,
                        "judge_run_id": judge_run_id,
                        "position": position.value,
                        "status": result.judge_run_status,
                        "normalized_winner": result.normalized_winner,
                        "confidence": result.confidence,
                        "latency_ms": result.latency_ms,
                    }
                )
            await session.commit()
        return {"ok": True, "case_id": case_id, "results": results}
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-experiment-id", required=True, type=UUID)
    parser.add_argument("--candidate-experiment-id", required=True, type=UUID)
    parser.add_argument("--case", required=True)
    args = parser.parse_args()
    result = asyncio.run(
        judge_existing(
            baseline_experiment_id=args.baseline_experiment_id,
            candidate_experiment_id=args.candidate_experiment_id,
            case_id=args.case,
        )
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
