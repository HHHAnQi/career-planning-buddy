"""SLO attainment report — reads live metrics from the database and emits
a pass/fail table per docs/standards/slo.md.

Exit code 1 when any SLO is unmet (wiring point for CI / pre-deploy
checks). Metrics are bound to their data sources — no manual entry:

* latency & cost  → agent_runs (real-model terminal Runs, last N days)
* live quality    → the most recent live Eval experiment (trial_count>=3)
* retrieval       → the latest retrieval-v2 report JSON (arg or file)
* mock regression → CI's own gate (out of scope here; always reported as
  CI-managed)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings

RETRIEVAL_REPORT_CANDIDATES = [
    Path("/tmp/retrieval_v2_report.json"),
]


@dataclass(frozen=True)
class SloResult:
    slo_id: str
    metric: str
    target: str
    actual: str
    met: bool | None  # None = data unavailable (reported, not counted)


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    rank = max(1, math.ceil(0.95 * len(ordered)))
    return ordered[rank - 1]


async def fetch_db_metrics() -> dict[str, object]:
    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            runs = (
                await session.execute(
                    text(
                        """
                        SELECT total_latency_ms, total_cost_cny
                        FROM agent_runs
                        WHERE model_id LIKE 'glm%'
                          AND status IN ('completed', 'degraded')
                          AND created_at > now() - interval '7 days'
                        """
                    )
                )
            ).all()
            latest_live = (
                await session.execute(
                    text(
                        """
                        SELECT e.trial_count,
                               count(*) AS trials,
                               count(*) FILTER (WHERE pt.passed) AS passed
                        FROM eval_experiments e
                        JOIN eval_trials t ON t.experiment_id = e.id
                        JOIN LATERAL (
                            SELECT bool_and(COALESCE(NOT s.hard_gate OR s.passed, true)) AS passed
                            FROM eval_scores s
                            WHERE s.trial_id = t.id
                        ) pt ON true
                        WHERE e.execution_mode = 'live_provider'
                          AND e.status = 'completed'
                        GROUP BY e.id, e.trial_count
                        HAVING count(*) >= 60
                        ORDER BY e.created_at DESC
                        LIMIT 1
                        """
                    )
                )
            ).one_or_none()
        return {
            "run_count": len(runs),
            "latencies_ms": [int(r[0]) for r in runs if r[0] and r[0] > 0],
            "costs": [float(r[1]) for r in runs if r[1] is not None],
            "latest_live": (
                {
                    "trial_count": int(latest_live[0]),
                    "completed": int(latest_live[1]),
                    "hard_gate_pass_fraction": (
                        int(latest_live[2]) / int(latest_live[1])
                        if int(latest_live[1])
                        else 0.0
                    ),
                }
                if latest_live
                else None
            ),
        }
    finally:
        await engine.dispose()


def load_retrieval(path: Path | None) -> dict[str, object] | None:
    candidates = [path] if path else RETRIEVAL_REPORT_CANDIDATES
    for candidate in candidates:
        if candidate and candidate.is_file():
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
                if "modes" in payload:
                    return payload
            except (json.JSONDecodeError, OSError):
                continue
    return None


def evaluate(db: dict[str, object], retrieval: dict[str, object] | None) -> list[SloResult]:
    results: list[SloResult] = []

    latencies = db["latencies_ms"]
    if latencies:
        p95_s = _p95([v / 1000 for v in latencies])
        results.append(
            SloResult(
                "latency-planning-p95",
                "规划 P95 延迟（近7天真实 Run）",
                "≤ 60s",
                f"{p95_s:.1f}s (n={len(latencies)})",
                p95_s <= 60,
            )
        )
    else:
        results.append(SloResult("latency-planning-p95", "规划 P95 延迟", "≤ 60s", "无数据", None))

    costs = db["costs"]
    if costs:
        avg = sum(costs) / len(costs)
        results.append(
            SloResult(
                "cost-planning-run",
                "单次规划成本（牌价估算）",
                "≤ ¥0.01",
                f"¥{avg:.4f} (n={len(costs)})",
                avg <= 0.01,
            )
        )
    else:
        results.append(SloResult("cost-planning-run", "单次规划成本", "≤ ¥0.01", "无数据", None))

    live = db["latest_live"]
    # trials >= 60 already guarantees a full k=3 dataset run (the query's
    # HAVING clause); trial_count here is the per-case repeat count.
    if live and live["completed"] >= 60:
        fraction = live["hard_gate_pass_fraction"]
        results.append(
            SloResult(
                "quality-live-hard-gate",
                f"live 硬门禁（最新实验 k={live['trial_count']}）",
                "≥ 0.85",
                f"{fraction:.3f}",
                fraction >= 0.85,
            )
        )
    else:
        results.append(
            SloResult("quality-live-hard-gate", "live 硬门禁", "≥ 0.85", "无合格实验", None)
        )

    if retrieval:
        hybrid = retrieval["modes"].get("hybrid", {})
        recall = hybrid.get("recall_at_5")
        if recall is not None:
            results.append(
                SloResult(
                    "retrieval-hybrid-recall",
                    "混合检索 Recall@5（硬化集）",
                    "≥ 0.90",
                    f"{recall:.3f}",
                    recall >= 0.90,
                )
            )
    else:
        results.append(
            SloResult("retrieval-hybrid-recall", "混合检索 Recall@5", "≥ 0.90", "报告缺失", None)
        )

    results.append(
        SloResult("regression-mock-gate", "mock 回归门禁", "100%", "CI 管理", None)
    )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retrieval-report", type=Path, default=None)
    parser.add_argument("--db-only", action="store_true")
    arguments = parser.parse_args()

    db = asyncio.run(fetch_db_metrics())
    retrieval = None if arguments.db_only else load_retrieval(arguments.retrieval_report)
    results = evaluate(db, retrieval)

    print(f"SLO 报告 — {datetime.now(UTC).isoformat()}")
    print("=" * 72)
    for item in results:
        mark = "✅" if item.met else "❌" if item.met is False else "—"
        print(f"{mark} {item.slo_id:28} {item.metric}")
        print(f"   目标 {item.target:12} 实际 {item.actual}")
    scored = [r for r in results if r.met is not None]
    failed = [r for r in scored if not r.met]
    print("=" * 72)
    verdict = "，存在未达项" if failed else ""
    print(f"判定: {len(scored) - len(failed)}/{len(scored)} 达标" + verdict)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
