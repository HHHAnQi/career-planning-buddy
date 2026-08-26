"""Blind D1-D4 quality review across two experiment arms (C2).

Loads the final plan of every replan / live-mem trial in the given
experiments, reconstructs the PlanCandidate, and scores it with the
independent DeepSeek rubric judge (blind: the judge sees only the request
and the plan, never the arm). Prints per-arm dimension means.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models.agent_run import AgentRun
from app.models.eval import EvalTrial
from app.models.plan import Plan, Task
from app.schemas.agent_runs import PlanCandidate, TaskCandidate, WeeklyFocusCandidate
from app.schemas.enums import TaskType
from evals.v2.dataset_loader import load_dataset
from evals.v2.rubric_judge import OpenAICompatibleRubricJudge, RubricJudgeInput

CASE_FILTER = ("replan", "live-mem")


async def _load_rows(factory, experiment_id: str):
    bundle = load_dataset()
    requests = {
        case.case_id: getattr(case.scenario, "user_request", "")
        for case in bundle.cases
        if case.case_id.startswith(CASE_FILTER)
    }
    async with factory() as session:
        trials = (
            await session.scalars(
                select(EvalTrial).where(
                    EvalTrial.experiment_id == experiment_id,
                    EvalTrial.status == "completed",
                )
            )
        ).all()
        rows = []
        for trial in trials:
            if not trial.case_id.startswith(CASE_FILTER):
                continue
            plan = await session.scalar(
                select(Plan).where(Plan.source_run_id == trial.run_id)
            )
            if plan is None:
                continue
            tasks = (
                await session.scalars(
                    select(Task).where(Task.plan_id == plan.id).order_by(Task.order_index)
                )
            ).all()
            rows.append(
                {
                    "case_id": trial.case_id,
                    "request": requests.get(trial.case_id, ""),
                    "plan": plan,
                    "tasks": tasks,
                }
            )
        return rows


def _candidate(row: dict) -> PlanCandidate:
    return PlanCandidate(
        plan_date=row["plan"].plan_date,
        horizon_start=row["plan"].horizon_start,
        horizon_end=row["plan"].horizon_end,
        overall_direction=row["plan"].overall_direction,
        weekly_focus=[
            WeeklyFocusCandidate(
                week_index=index + 1,
                focus=(row["plan"].overall_direction or "goal")[:40],
                success_signal=(row["plan"].overall_direction or "goal")[:40],
            )
            for index in range(
                max(
                    1,
                    (row["plan"].horizon_end - row["plan"].horizon_start).days // 7,
                )
            )
        ],
        summary=row["plan"].summary or "",
        rationale=row["plan"].rationale or "",
        tasks=[
            TaskCandidate(
                title=task.title,
                task_type=TaskType(task.task_type),
                scheduled_date=task.scheduled_date,
                starter_action=task.starter_action or "",
                deliverable=task.deliverable or "",
                estimated_minutes=task.estimated_minutes,
                rationale=task.rationale or "",
            )
            for task in row["tasks"]
        ],
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("arm_a", help="experiment id (label A)")
    parser.add_argument("arm_b", help="experiment id (label B)")
    parser.add_argument("--label-a", default="A")
    parser.add_argument("--label-b", default="B")
    args = parser.parse_args()

    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    judge = OpenAICompatibleRubricJudge(
        api_key=settings.judge_llm_api_key.get_secret_value(),
        base_url=str(settings.judge_llm_base_url),
        model=settings.judge_llm_model,
        disable_thinking="deepseek" in str(settings.judge_llm_base_url),
    )
    out: dict[str, object] = {}
    try:
        for label, exp in ((args.label_a, args.arm_a), (args.label_b, args.arm_b)):
            rows = await _load_rows(factory, exp)
            scores: dict[str, list[int]] = {
                "D1_goal_alignment": [],
                "D2_evidence_grounding": [],
                "D3_executability": [],
                "D4_horizon_compliance": [],
            }
            details = []
            for row in rows:
                prompt = RubricJudgeInput(
                    request_message=row["request"],
                    profile_summary="job-search user (blind review)",
                    time_budget_minutes=90,
                    candidate=_candidate(row),
                )
                result = await judge.score(prompt)
                values = {
                    "D1_goal_alignment": result.goal_alignment,
                    "D2_evidence_grounding": result.evidence_grounding,
                    "D3_executability": result.executability,
                    "D4_horizon_compliance": result.horizon_compliance,
                }
                for key, value in values.items():
                    if value is not None:
                        scores[key].append(value)
                details.append({"case_id": row["case_id"], **values})
            out[label] = {
                "experiment_id": exp,
                "trial_count": len(rows),
                "dimension_means": {
                    key: round(sum(v) / len(v), 2) if v else None
                    for key, v in scores.items()
                },
                "details": details,
            }
    finally:
        await engine.dispose()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
