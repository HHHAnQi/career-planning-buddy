"""Read-only memory_grounded report for a completed experiment.

Computes the same check as the ``model.memory_grounded`` grader (plan text
must lexically hit >= half of the planted relevant memories, bigram ratio
>= 0.10) WITHOUT persisting scores — use for already-graded experiments
where re-grading would collide with the all-or-nothing score insert.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models.agent_run import AgentRun
from app.models.eval import EvalTrial
from app.models.plan import Plan, Task
from evals.v2.dataset_loader import load_dataset

THRESHOLD = 0.10


def _bigrams(text: str) -> set[str]:
    normalized = "".join(text.split()).lower()
    return {normalized[i : i + 2] for i in range(len(normalized) - 1)} if len(normalized) >= 2 else set()


async def build_report(experiment_id: str) -> dict[str, object]:
    bundle = load_dataset()
    cases = {case.case_id: case for case in bundle.cases}
    # confirmed_memories live on the scenario; the strict EvalCase wraps it.
    scenario_memories = {}
    for case in bundle.cases:
        scenario = getattr(case, "scenario", None)
        memories = getattr(scenario, "confirmed_memories", None)
        if memories is None and hasattr(case, "confirmed_memories"):
            memories = case.confirmed_memories
        scenario_memories[case.case_id] = memories or []
    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    rows: list[dict[str, object]] = []
    try:
        async with factory() as session:
            trials = (
                await session.execute(
                    select(EvalTrial).where(
                        EvalTrial.experiment_id == experiment_id,
                        EvalTrial.status == "completed",
                        EvalTrial.run_id.is_not(None),
                    )
                )
            ).scalars()
            for trial in trials:
                case = cases.get(trial.case_id)
                if case is None:
                    continue
                memories = [
                    str(item.get("content", "")).strip()
                    for item in scenario_memories.get(trial.case_id, [])
                    if str(item.get("category", "relevant")) == "relevant"
                    and str(item.get("content", "")).strip()
                ]
                if not memories:
                    continue
                run = await session.get(AgentRun, trial.run_id)
                plan = (
                    await session.scalar(
                        select(Plan).where(Plan.source_run_id == run.id)
                    )
                    if run is not None
                    else None
                )
                if plan is None:
                    rows.append(
                        {"case_id": trial.case_id, "grounded": 0,
                         "planted": len(memories), "note": "no plan"}
                    )
                    continue
                tasks = (
                    await session.scalars(
                        select(Task).where(Task.plan_id == plan.id)
                    )
                ).all()
                plan_text = " ".join(
                    part
                    for part in (plan.summary, plan.rationale or "")
                    if part
                ) + " " + " ".join(
                    f"{t.title} {t.deliverable}" for t in tasks
                )
                plan_bigrams = _bigrams(plan_text)
                grounded = sum(
                    1
                    for memory in memories
                    if (mb := _bigrams(memory))
                    and len(mb & plan_bigrams) / len(mb) >= THRESHOLD
                )
                rows.append(
                    {"case_id": trial.case_id, "grounded": grounded,
                     "planted": len(memories)}
                )
    finally:
        await engine.dispose()
    graded = [r for r in rows if r["planted"]]
    passed = sum(
        1 for r in graded if r["grounded"] >= max(1, (r["planted"] + 1) // 2)
    )
    return {
        "experiment_id": experiment_id,
        "trial_rows": len(rows),
        "memory_grounded_pass": passed,
        "graded_trials": len(graded),
        "details": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiment_id")
    args = parser.parse_args()
    report = asyncio.run(build_report(args.experiment_id))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
