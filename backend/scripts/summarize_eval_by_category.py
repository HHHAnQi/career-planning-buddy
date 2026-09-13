"""Per-category pass-rate summary for the latest Eval V2 experiment.

Groups the most recent experiment's cases by category prefix
(create / repair / replan / clarify / safe / live-mem …) and prints each
category's hard-gate pass count, trial pass rate, and mean latency.

Usage::

    python -m scripts.summarize_eval_by_category            # latest experiment
    python -m scripts.summarize_eval_by_category --experiment-id <uuid>
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings


async def summarize(experiment_id: UUID | None) -> dict[str, object]:
    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            if experiment_id is None:
                experiment_id = await session.scalar(
                    text("SELECT id FROM eval_experiments ORDER BY created_at DESC LIMIT 1")
                )
            if experiment_id is None:
                raise SystemExit("no experiments found")
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT case_id,
                               count(*) AS trials,
                               count(*) FILTER (WHERE trial_passed) AS passed,
                               avg(avg_latency_ms) AS avg_latency
                        FROM (
                            SELECT t.id,
                                   t.case_id,
                                   r.total_latency_ms AS avg_latency_ms,
                                   bool_and(COALESCE(NOT s.hard_gate OR s.passed, true))
                                       AS trial_passed
                            FROM eval_trials t
                            LEFT JOIN agent_runs r ON r.id = t.run_id
                            LEFT JOIN eval_scores s ON s.trial_id = t.id
                            WHERE t.experiment_id = :eid
                            GROUP BY t.id, t.case_id, r.total_latency_ms
                        ) per_trial
                        GROUP BY case_id
                        ORDER BY case_id
                        """
                    ),
                    {"eid": experiment_id},
                )
            ).all()
        categories: dict[str, dict[str, float]] = {}
        for case_id, trials, passed, avg_latency in rows:
            category = case_id.rsplit("-", 1)[0]
            bucket = categories.setdefault(
                category, {"trials": 0.0, "passed": 0.0, "latency_total": 0.0, "cases": 0.0}
            )
            bucket["trials"] += int(trials or 0)
            bucket["passed"] += int(passed or 0)
            bucket["latency_total"] += float(avg_latency or 0) * int(trials or 0)
            bucket["cases"] += 1
        return {
            "experiment_id": str(experiment_id),
            "generated_at": datetime.now(UTC).isoformat(),
            "categories": {
                name: {
                    "cases": int(bucket["cases"]),
                    "trials": int(bucket["trials"]),
                    "passed": int(bucket["passed"]),
                    "pass_rate": round(bucket["passed"] / bucket["trials"], 4)
                    if bucket["trials"]
                    else 0.0,
                    "avg_latency_ms": int(bucket["latency_total"] / bucket["trials"])
                    if bucket["trials"]
                    else 0,
                }
                for name, bucket in sorted(categories.items())
            },
        }
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-id", type=UUID, default=None)
    arguments = parser.parse_args()
    import json

    print(json.dumps(asyncio.run(summarize(arguments.experiment_id)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
