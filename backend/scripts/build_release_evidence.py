"""Build a public, redacted release evidence package for one experiment.

Generates evals/releases/<name>/ with manifest, frozen config, case and
trial records (anonymized), grades, summary, failure breakdown, and a
SHA256SUMS file. No API keys, no personal data: user ids are salted-hash
anonymized, request texts are the public dataset's own case messages.
Reproducible: rerun against the same experiment to regenerate identical
trial rows (hashes are content-derived).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings

ANON_SALT = "career-buddy-release-evidence-v1"


def _anon(value: str) -> str:
    return hashlib.sha256(f"{ANON_SALT}:{value}".encode()).hexdigest()[:16]


async def build(experiment_id: str, out_dir: Path) -> None:
    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    out_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
    try:
        async with factory() as session:
            experiment = (
                await session.execute(
                    text(
                        "SELECT id, dataset_id, dataset_version, execution_mode,"
                        " agent_variant, graph_version, prompt_version, model_version,"
                        " trial_count, status, created_at, finished_at"
                        " FROM eval_experiments WHERE id = CAST(:e AS uuid)"
                    ),
                    {"e": experiment_id},
                )
            ).one()

            trials = (
                await session.execute(
                    text(
                        """
                        SELECT t.id, t.case_id, t.trial_index, t.status, t.run_id,
                               t.tokens_in, t.tokens_out, t.latency_ms,
                               (SELECT e.payload_json ->> 'plan_provenance'
                                  FROM agent_events e
                                 WHERE e.run_id = t.run_id
                                   AND e.event_type = 'run.provenance'
                                 ORDER BY e.sequence DESC LIMIT 1) AS provenance,
                               bool_and(COALESCE(NOT s.hard_gate OR s.passed, true))
                                   AS hard_gate_passed
                        FROM eval_trials t
                        JOIN eval_scores s ON s.trial_id = t.id
                        WHERE t.experiment_id = CAST(:e AS uuid)
                          AND t.status = 'completed'
                        GROUP BY t.id, t.case_id, t.trial_index, t.status,
                                 t.run_id, t.tokens_in, t.tokens_out, t.latency_ms
                        ORDER BY t.case_id, t.trial_index
                        """
                    ),
                    {"e": experiment_id},
                )
            ).all()

            grades = (
                await session.execute(
                    text(
                        """
                        SELECT t.case_id, t.trial_index, s.grader_name, s.domain,
                               s.metric_type, s.passed, s.hard_gate
                        FROM eval_trials t JOIN eval_scores s ON s.trial_id = t.id
                        WHERE t.experiment_id = CAST(:e AS uuid)
                          AND t.status = 'completed'
                        ORDER BY t.case_id, t.trial_index, s.grader_name
                        """
                    ),
                    {"e": experiment_id},
                )
            ).all()

            config_row = (
                await session.execute(
                    text(
                        """
                        SELECT r.config_snapshot_json
                        FROM eval_trials t JOIN agent_runs r ON r.id = t.run_id
                        WHERE t.experiment_id = CAST(:e AS uuid)
                          AND t.status = 'completed'
                        ORDER BY t.trial_index LIMIT 1
                        """
                    ),
                    {"e": experiment_id},
                )
            ).scalar_one_or_none()

            failures = (
                await session.execute(
                    text(
                        """
                        SELECT t.case_id, t.trial_index, s.grader_name
                        FROM eval_trials t JOIN eval_scores s ON s.trial_id = t.id
                        WHERE t.experiment_id = CAST(:e AS uuid)
                          AND s.passed = false AND s.hard_gate
                        ORDER BY t.case_id, t.trial_index
                        """
                    ),
                    {"e": experiment_id},
                )
            ).all()
    finally:
        await engine.dispose()

    def dump(name: str, payload: object) -> None:
        target = out_dir / name
        if isinstance(payload, str):
            target.write_text(payload, encoding="utf-8")
        else:
            target.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    from datetime import datetime as _dt

    def _ser(value):
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, _dt):
            return value.isoformat()
        return value

    manifest = {key: _ser(value) for key, value in experiment._mapping.items()}
    dump("experiment_manifest.json", manifest)
    dump(
        "frozen_config.json",
        (config_row or {}) if isinstance(config_row, dict) else {},
    )

    trial_rows = []
    for row in trials:
        trial_rows.append(
            {
                "case_id": row.case_id,
                "trial_index": row.trial_index,
                "status": row.status,
                "run_id_anon": _anon(str(row.run_id)),
                "tokens_in": row.tokens_in,
                "tokens_out": row.tokens_out,
                "latency_ms": row.latency_ms,
                "plan_provenance": row.provenance,
                "hard_gate_passed": row.hard_gate_passed,
            }
        )
    with (out_dir / "anonymized_trials.jsonl").open("w", encoding="utf-8") as fh:
        for row in trial_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    grade_rows = [
        {
            "case_id": g.case_id,
            "trial_index": g.trial_index,
            "grader": g.grader_name,
            "domain": g.domain,
            "passed": g.passed,
            "hard_gate": g.hard_gate,
        }
        for g in grades
    ]
    with (out_dir / "grades.jsonl").open("w", encoding="utf-8") as fh:
        for row in grade_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    completed = [r for r in trial_rows]
    passed = [r for r in completed if r["hard_gate_passed"]]
    latencies = sorted(r["latency_ms"] for r in completed)

    def pct(p: float) -> int:
        return latencies[max(0, min(len(latencies) - 1, int(p * len(latencies))))]

    provenance_split: dict[str, int] = {}
    for row in completed:
        label = row["plan_provenance"] or "no_plan_path"
        provenance_split[label] = provenance_split.get(label, 0) + 1

    failure_map: dict[str, list[str]] = {}
    for f in failures:
        key = f"{f.case_id}#{f.trial_index}"
        failure_map.setdefault(key, []).append(f.grader_name)

    summary = {
        "experiment_id": manifest["id"],
        "dataset": f"{manifest['dataset_id']}@{manifest['dataset_version']}",
        "model": manifest["model_version"],
        "trials_completed": len(completed),
        "hard_gate_passed": len(passed),
        "hard_gate_rate": round(len(passed) / len(completed), 4) if completed else None,
        "latency_p50_ms": pct(0.50) if latencies else None,
        "latency_p95_ms": pct(0.95) if latencies else None,
        "provenance_split": provenance_split,
        "failing_trials": failure_map,
        "notes": (
            "user ids salted-hash anonymized; grades reproducible via "
            "scripts/confidence_report.py against the same experiment"
        ),
    }
    dump("summary.json", summary)
    dump(
        "failure_breakdown.json",
        {
            "failing_trial_count": len(failure_map),
            "by_trial": failure_map,
            "grader_histogram": {
                name: sum(1 for v in failure_map.values() if name in v)
                for name in sorted({g for v in failure_map.values() for g in v})
            },
        },
    )
    cases = sorted({r["case_id"] for r in trial_rows})
    dump(
        "case_manifest.json",
        {"case_count": len(cases), "case_ids": cases},
    )

    hashes = []
    for path in sorted(out_dir.iterdir()):  # noqa: ASYNC240
        if path.name == "SHA256SUMS":
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()  # noqa: ASYNC240
        hashes.append(f"{digest}  {path.name}")
    (out_dir / "SHA256SUMS").write_text("\n".join(hashes) + "\n", encoding="utf-8")
    print(f"wrote {len(hashes) + 1} files to {out_dir}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiment_id")
    parser.add_argument("--name", default=None, help="release directory name")
    args = parser.parse_args()
    out = Path("evals/releases") / (args.name or args.experiment_id[:8])
    out.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
    asyncio.run(build(args.experiment_id, out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
