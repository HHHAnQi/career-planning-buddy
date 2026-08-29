"""Build the FRESH rubric annotation worksheet for anchor re-validation.

Generates 10 fresh planning outputs (real GLM on NEW paraphrase-style
cases never used in the original 23-row worksheet) and exports both the
JSONL worksheet and the Excel-friendly CSV for two-annotator labeling.

This discharges the self-contamination risk of the anchor calibration:
anchors were derived from disagreements on the ORIGINAL 23 rows; this
fresh set tests whether the anchored rubric produces human-human
kappa >= 0.7 on unseen data.
"""

from __future__ import annotations

import asyncio
import csv
import json
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from app.agent.nodes import fallback_candidate
from app.core.config import get_settings
from app.providers.llm import build_planning_provider
from app.schemas.agent_runs import (
    PlanningContext,
    PlanningWindow,
    ProfileContext,
    ProviderPlanResponse,
)

FRESH_CASES = [
    {"case_id": "fresh-01", "message": "帮我安排下找工作的日程，主要是简历这块还没弄好",
     "goal_type": "agent_app", "stage": "preparing", "budget": 60, "level": "intermediate"},
    {"case_id": "fresh-02", "message": "下周想集中突击面试，给我排一个计划",
     "goal_type": "agent_app", "stage": "applying", "budget": 90, "level": "beginner"},
    {"case_id": "fresh-03", "message": "转行做AI方向，零基础开始，怎么规划这段时间",
     "goal_type": "fullstack", "stage": "exploring", "budget": 120, "level": "beginner"},
    {"case_id": "fresh-04", "message": "论文写完了有三个月空闲，想全力准备秋招",
     "goal_type": "agent_app", "stage": "preparing", "budget": 240, "level": "advanced"},
    {"case_id": "fresh-05", "message": "白天上班只有晚上有时间学，帮我安排下",
     "goal_type": "fullstack", "stage": "preparing", "budget": 45, "level": "intermediate"},
    {"case_id": "fresh-06", "message": "实习找得不太顺，想重新梳理一下策略",
     "goal_type": "agent_app", "stage": "applying", "budget": 60, "level": "intermediate"},
    {"case_id": "fresh-07", "message": "前端转全栈，要补后端知识，怎么安排每天的学习",
     "goal_type": "fullstack", "stage": "preparing", "budget": 90, "level": "intermediate"},
    {"case_id": "fresh-08", "message": "手上有两个offer在比，同时还要准备终面",
     "goal_type": "agent_app", "stage": "interviewing", "budget": 30, "level": "advanced"},
    {"case_id": "fresh-09", "message": "算法基础比较薄弱，想刷题加上项目一起搞",
     "goal_type": "ai_backend", "stage": "preparing", "budget": 150, "level": "intermediate"},
    {"case_id": "fresh-10", "message": "秋招提前批开始了，时间很紧，帮我冲刺一下",
     "goal_type": "agent_app", "stage": "applying", "budget": 180, "level": "advanced"},
]

OUTPUT_JSONL = Path("evals/annotations/rubric-fresh-worksheet.jsonl")
OUTPUT_CSV = Path("evals/annotations/rubric-fresh-worksheet.csv")


async def generate() -> int:
    settings = get_settings()
    provider = build_planning_provider(settings)
    rows: list[dict[str, object]] = []

    for case in FRESH_CASES:
        today = date(2026, 9, 1)
        profile = ProfileContext(
            user_id=__import__("uuid").uuid4(),
            version=1,
            goal_type=case["goal_type"],
            stage=case["stage"],
            time_budget_minutes=case["budget"],
            skill_level=case["level"],
            skill_summary="fresh eval case",
        )
        context = PlanningContext(
            profile=profile,
            planning_window=PlanningWindow(
                planning_date=today, horizon_start=today,
                horizon_end=today, horizon_weeks=1,
            ),
            time_budget_minutes=case["budget"],
            token_estimate=100,
        )
        raw = await provider.generate_plan(
            message=case["message"], context=context,
            replan_mode="initial", evidence_catalog=[],
        )
        try:
            candidate = ProviderPlanResponse.model_validate(raw).candidate
        except ValidationError:
            try:
                repaired = await provider.repair_format(
                    raw_output=raw, context=context,
                    replan_mode="initial", evidence_catalog=[],
                )
                candidate = ProviderPlanResponse.model_validate(repaired).candidate
            except ValidationError:
                candidate = fallback_candidate(context, "initial")

        rows.append({
            "case_id": case["case_id"],
            "rubric_version": "plan-quality-rubric-v1-anchored",
            "provider": settings.llm_provider,
            "request_message": case["message"],
            "profile_summary": f"{case['goal_type']} / {case['stage']} / {case['level']}"
                f" / {case['budget']}min/day",
            "time_budget_minutes": case["budget"],
            "evidence_catalog_ids": [],
            "candidate": candidate.model_dump(mode="json"),
            "annotations": None,
        })
        print(f"  {case['case_id']} ✓")


    return rows


def main() -> int:
    asyncio.run(generate())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def _write_outputs(rows: list[dict[str, object]]) -> None:
    OUTPUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_JSONL.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # Excel-friendly CSV
    with OUTPUT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "case_id", "请求", "画像", "时间预算", "规划摘要", "任务速览",
            "D1_目标对齐(1-5)", "D2_证据支撑(1-5)", "D3_可执行性(1-5)",
            "D4_周期合规(1-5)", "理由(必填)", "标注人", "标注日期",
        ])
        for row in rows:
            cand = row["candidate"]
            tasks = "\n".join(
                f"{t['scheduled_date']} | {t['title']} | {t['starter_action'][:50]}"
                for t in cand.get("tasks", [])
            )
            writer.writerow([
                row["case_id"], row["request_message"], row["profile_summary"],
                row["time_budget_minutes"], cand.get("summary", ""), tasks,
                "", "", "", "", "", "", "",
            ])

    print(f"\n{len(rows)} rows → {OUTPUT_JSONL}")
    print(f"Excel CSV → {OUTPUT_CSV}")
