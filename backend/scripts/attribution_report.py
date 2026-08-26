"""Attribution report — split hard-gate pass rate into model vs engineering
contribution.

Reads the latest live Eval experiment (or --experiment-id) and joins each
completed trial's hard-gate outcome with the run's ``run.provenance``
event written by the graph's persist node:

* model_pass          — the model's first-shot candidate passed validation
* format_repair       — provider format repair produced the final candidate
* deterministic_repair — code patched business-rule violations (no LLM call)
* llm_repair          — the model repaired its own business-rule violations
* fallback            — deterministic degrade template produced the result

This is the answer to "how much of your 92% is the model and how much is
your code": both are reported, on the same trials, from durable evidence.
Exit code 1 when no attributable trials are found.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings

PROVENANCE_LABELS = {
    "model_pass": "模型一次通过（Agent 智能能力）",
    "format_repair": "格式修复（模型自修复，工程触发）",
    "deterministic_repair": "确定性规则修复（工程贡献）",
    "llm_repair": "模型业务规则自修复（Agent 智能能力）",
    "fallback": "确定性降级模板（工程兜底）",
}

QUERY = text(
    """
    SELECT t.id AS trial_id,
           t.case_id,
           t.trial_index,
           t.run_id,
           bool_and(s.passed) FILTER (WHERE s.hard_gate) AS hard_gate_passed,
           (SELECT e.payload_json ->> 'plan_provenance'
              FROM agent_events e
             WHERE e.run_id = t.run_id
               AND e.event_type = 'run.provenance'
             ORDER BY e.sequence DESC
             LIMIT 1) AS provenance,
           (SELECT e.payload_json -> 'unknown_rule_codes'
              FROM agent_events e
             WHERE e.run_id = t.run_id
               AND e.event_type = 'run.provenance'
             ORDER BY e.sequence DESC
             LIMIT 1) AS unknown_rule_codes
      FROM eval_trials t
      JOIN eval_scores s ON s.trial_id = t.id
      JOIN eval_experiments x ON x.id = t.experiment_id
     WHERE (CAST(:experiment_id AS uuid) IS NULL AND x.id = (
                SELECT id FROM eval_experiments
                 WHERE execution_mode = 'live_provider'
                   AND status = 'completed'
                 ORDER BY created_at DESC LIMIT 1)
            OR x.id = CAST(:experiment_id AS uuid))
       AND t.status = 'completed'
       AND t.run_id IS NOT NULL
     GROUP BY t.id, t.case_id, t.trial_index, t.run_id
    """
)


async def build_report(experiment_id: str | None) -> dict[str, object]:
    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            rows = (await session.execute(QUERY, {"experiment_id": experiment_id})).all()
    finally:
        await engine.dispose()

    trials = [row for row in rows if row.provenance is not None]
    unattributable = len(rows) - len(trials)
    passed = [row for row in trials if row.hard_gate_passed]
    failed = [row for row in trials if not row.hard_gate_passed]
    pass_split = Counter(row.provenance for row in passed)
    fail_split = Counter(row.provenance for row in failed)
    unknown_rules = Counter(
        code
        for row in trials
        for code in (row.unknown_rule_codes or [])
    )
    return {
        "trial_count": len(rows),
        "attributable_count": len(trials),
        "unattributable_count": unattributable,
        "hard_gate_pass_count": len(passed),
        "hard_gate_pass_rate": round(len(passed) / len(trials), 4) if trials else None,
        "pass_attribution": dict(pass_split),
        "fail_attribution": dict(fail_split),
        "model_share_of_passes": (
            round(
                (pass_split.get("model_pass", 0) + pass_split.get("llm_repair", 0))
                / len(passed),
                4,
            )
            if passed
            else None
        ),
        "engineering_share_of_passes": (
            round(
                (
                    pass_split.get("deterministic_repair", 0)
                    + pass_split.get("fallback", 0)
                    + pass_split.get("format_repair", 0)
                )
                / len(passed),
                4,
            )
            if passed
            else None
        ),
        "unknown_rule_backlog": dict(unknown_rules),
    }


def render(report: dict[str, object]) -> str:
    lines = [
        "=== 双维度归因报告（模型能力 vs 工程兜底） ===",
        f"实验 trial 总数: {report['trial_count']}"
        f"（可归因 {report['attributable_count']}"
        f" / 无 provenance 事件 {report['unattributable_count']}）",
        f"硬门禁通过: {report['hard_gate_pass_count']}"
        f" / {report['attributable_count']}"
        f" = {report['hard_gate_pass_rate']}",
        "",
        "通过 case 的产出路径拆分:",
    ]
    for key, count in sorted(
        report["pass_attribution"].items(), key=lambda kv: -kv[1]
    ):
        label = PROVENANCE_LABELS.get(key, key)
        lines.append(f"  {key:<22} {count:>4}  {label}")
    lines += [
        "",
        f"模型能力占比（model_pass + llm_repair）: {report['model_share_of_passes']}",
        f"工程兜底占比（deterministic + format + fallback）: "
        f"{report['engineering_share_of_passes']}",
        "",
        "失败 case 的路径拆分:",
    ]
    for key, count in sorted(
        report["fail_attribution"].items(), key=lambda kv: -kv[1]
    ):
        lines.append(f"  {key:<22} {count:>4}")
    backlog = report["unknown_rule_backlog"]
    if backlog:
        lines += ["", "未知规则沉淀（离线迭代候选）:"]
        for code, count in sorted(backlog.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {code:<32} {count:>4}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--experiment-id",
        default=None,
        help="Specific experiment UUID (default: latest live experiment)",
    )
    args = parser.parse_args()
    report = asyncio.run(build_report(args.experiment_id))
    print(render(report))
    return 0 if report["attributable_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
