"""Confidence report: every headline number gets a Wilson 95% interval,
and every A/B comparison gets a two-proportion test.

Motivation (interview round 2, Q3): conclusions like "+13.3pp from the
memory layer" were drawn from single k=1 runs while the same case
(live-mem-01) was observed flipping between runs — all single-run
conclusions swim in variance. This script makes the uncertainty visible:
any headline number without a CI is, by policy, not a claim.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings

QUERY = text(
    """
    WITH pt AS (
        SELECT t.id, t.case_id,
               bool_and(COALESCE(NOT s.hard_gate OR s.passed, true)) AS passed
        FROM eval_trials t
        JOIN eval_scores s ON s.trial_id = t.id
        WHERE t.experiment_id = CAST(:experiment_id AS uuid) AND t.status = 'completed'
        GROUP BY t.id, t.case_id
    )
    SELECT count(*)::int AS n, count(*) FILTER (WHERE passed)::int AS k
    FROM pt
    """
)


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    """Wilson score interval for a binomial proportion."""
    if n == 0:
        return 0.0, 0.0, 0.0
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return p, max(0.0, center - half), min(1.0, center + half)


def two_proportion_test(k1: int, n1: int, k2: int, n2: int) -> dict[str, float]:
    """Pooled z-test for two proportions (sufficient at these n)."""
    p1, p2 = k1 / n1, k2 / n2
    pooled = (k1 + k2) / (n1 + n2)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    z = (p1 - p2) / se if se > 0 else 0.0
    # Two-sided p-value via the error-function approximation of the CDF.
    p_value = math.erfc(abs(z) / math.sqrt(2))
    return {
        "p1": round(p1, 4),
        "p2": round(p2, 4),
        "diff_pp": round((p1 - p2) * 100, 1),
        "z": round(z, 2),
        "p_value": round(p_value, 4),
        "significant_at_0.05": p_value < 0.05,
    }


async def fetch(engine, experiment_id: str) -> tuple[int, int]:
    async with engine.connect() as conn:
        row = (await conn.execute(QUERY, {"experiment_id": experiment_id})).one()
        return int(row.k), int(row.n)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiment_id")
    parser.add_argument("--compare-to", default=None, help="second experiment id")
    args = parser.parse_args()

    engine = create_async_engine(get_settings().database_url)
    try:
        k, n = await fetch(engine, args.experiment_id)
        p, low, high = wilson_ci(k, n)
        report: dict[str, object] = {
            "experiment_id": args.experiment_id,
            "n": n,
            "passed": k,
            "rate": round(p, 4),
            "wilson_95": [round(low, 4), round(high, 4)],
        }
        if args.compare_to:
            k2, n2 = await fetch(engine, args.compare_to)
            p2, low2, high2 = wilson_ci(k2, n2)
            report["comparison"] = {
                "other_experiment_id": args.compare_to,
                "other": {"n": n2, "passed": k2, "rate": round(p2, 4),
                          "wilson_95": [round(low2, 4), round(high2, 4)]},
                "test": two_proportion_test(k, n, k2, n2),
            }
    finally:
        await engine.dispose()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
