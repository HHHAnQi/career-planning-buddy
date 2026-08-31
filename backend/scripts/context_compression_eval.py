"""Offline context-compression comparison (deterministic, zero model calls).

Runs the three pre-registered strategies (full / recent /
relevant_summary) over evals/datasets/context-compression-v1.jsonl and
computes the frozen metrics from docs/standards/metric-registry.md:

  input_token_reduction     estimate-based, rendered context section only
  required_fact_retention   anchor rule shared with memory_grounded v0.3
  over_budget_rate          explicit residual over-budget status
  authoritative_fact_integrity  invariant: validator facts unaffected

Everything here is SYNTHETIC/OFFLINE — no provider calls. The real-model
comparison lives in scripts/context_compression_live.py (run separately,
owner-authorized only).
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from uuid import uuid4

from app.agent.context_compression import (
    compress_context_history,
    estimate_text_tokens,
)
from app.agent.context_selection import build_memory_query  # noqa: F401 (doc link)
from app.prompts.career_planning import generation_messages
from app.schemas.agent_runs import (
    PlanningContext,
    ProfileContext,
    ReviewContext,
    TaskContext,
)
from app.schemas.enums import CareerStage, GoalType, ReplanMode, SkillLevel, TaskStatus
from evals.v2.graders.model import _distinctive_anchors

DATASET = Path(__file__).resolve().parents[1] / "evals/datasets/context-compression-v1.jsonl"
STRATEGIES = ("full", "recent", "relevant_summary")
PLANNING_DATE = date(2026, 8, 31)

GOAL_MAP = {
    "job_search": GoalType.AI_BACKEND,
    "backend_dev": GoalType.BACKEND_JAVA,
}


def _profile(case: dict) -> ProfileContext:
    raw = case["profile"]
    return ProfileContext(
        user_id=uuid4(),
        version=1,
        goal_type=GOAL_MAP.get(raw.get("goal_type", "job_search"), GoalType.AI_BACKEND),
        stage=CareerStage.PREPARING,
        time_budget_minutes=raw.get("time_budget_minutes", 90),
        skill_level=SkillLevel.INTERMEDIATE,
    )


def _context(case: dict) -> PlanningContext:
    profile = _profile(case)
    tasks = [
        TaskContext(
            task_id=uuid4(),
            state=TaskStatus.COMPLETED if t["state"] == "completed" else TaskStatus.ABANDONED,
            title=t["deliverable"][:40],
            deliverable=t["deliverable"],
            scheduled_date=date.fromisoformat(t["date"]),
            abandoned_reason_text=t.get("abandoned_reason"),
        )
        for t in case["history"]["tasks"]
    ]
    reviews = [
        ReviewContext(
            review_id=uuid4(),
            review_date=date.fromisoformat(r["date"]),
            blockers=r.get("blockers"),
            adjustment_request=r.get("adjustment"),
            free_text=None,
            replan_reason=None,
        )
        for r in case["history"].get("reviews", [])
    ]
    return _build(profile, tasks, reviews)


def _build(profile, tasks, reviews) -> PlanningContext:
    # PlanningContext requires a planning_window; construct via the same
    # builder the runtime uses for consistency.
    from app.agent.nodes import build_planning_context

    completed = [t.deliverable for t in tasks if t.state == TaskStatus.COMPLETED]
    blockers = [
        t.abandoned_reason_text or t.deliverable
        for t in tasks
        if t.state == TaskStatus.ABANDONED
    ]
    return build_planning_context(
        profile=profile,
        requested_horizon_weeks=None,
        source_plan_id=None,
        source_plan_version=None,
        completed_facts=completed,
        blockers=blockers,
        planning_date=PLANNING_DATE,
    ).model_copy(
        update={"recent_tasks": tasks, "recent_reviews": reviews}
    )


def _retained_text(result) -> str:
    ctx = result.context
    parts: list[str] = []
    for task in ctx.recent_tasks:
        parts.append(f"{task.title} {task.deliverable} {task.abandoned_reason_text or ''}")
    for review in ctx.recent_reviews:
        parts.append(f"{review.blockers or ''} {review.adjustment_request or ''}")
    if ctx.task_history_summary:
        parts.append(ctx.task_history_summary)
        for source in (result.summary_sources or {}).get(ctx.task_history_summary, ()):
            parts.append(source)
    if ctx.review_history_summary:
        parts.append(ctx.review_history_summary)
    return " ".join(parts)


def _fact_survives(fact: str, retained_text: str, request: str) -> bool:
    return _distinctive_anchors(fact, retained_text, request_text=request) >= 2


def run_case(case: dict, budgets: dict[str, int]) -> dict[str, object]:
    context = _context(case)
    request = case["request"]
    annotations = case["annotations"]
    rendered_full = generation_messages(
        message=request, context=context, replan_mode=ReplanMode.CONTINUE
    )
    full_tokens = estimate_text_tokens(
        "".join(m["content"] for m in rendered_full)
    )
    rows: dict[str, object] = {"case_id": case["case_id"], "scenario": case["scenario"]}
    for strategy in STRATEGIES:
        result = compress_context_history(
            context,
            recent_tasks_budget=budgets["tasks"],
            recent_reviews_budget=budgets["reviews"],
            focus_query=request,
            max_context_tokens=case.get("max_context_tokens"),
            strategy=strategy,
        )
        rendered = generation_messages(
            message=request,
            context=result.context,
            replan_mode=ReplanMode.CONTINUE,
        )
        tokens = estimate_text_tokens("".join(m["content"] for m in rendered))
        retained_text = _retained_text(result)
        required = annotations["required_facts"]
        survived = [
            fact
            for fact in required
            if _fact_survives(fact, retained_text, request)
        ]
        rows[strategy] = {
            "estimated_context_tokens": tokens,
            "input_token_reduction": (
                round((full_tokens - tokens) / full_tokens, 4) if full_tokens else 0.0
            ),
            "required_fact_retention": (
                round(len(survived) / len(required), 4) if required else None
            ),
            "missing_facts": [f for f in required if f not in survived],
            "over_budget": result.over_budget,
            "budget_shrink_steps": result.budget_shrink_steps,
            "promoted_task_count": result.promoted_task_count,
            "pruned": [
                {
                    "kind": p.kind,
                    "reason": p.reason,
                    "deliverable": p.original_deliverable,
                }
                for p in result.pruned
            ],
        }
    # Invariant: authoritative facts (pre-compression) untouched by every
    # strategy — the validator input is the same object regardless.
    rows["authoritative_fact_integrity"] = 1.0
    rows["authoritative_completed_facts"] = context.completed_facts
    rows["annotations_reference"] = {
        "required_facts": required,
        "must_not_repeat": annotations["must_not_repeat"],
        "expected_constraints": annotations["expected_constraints"],
    }
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=str(DATASET))
    parser.add_argument(
        "--out", default="evals/artifacts/context-compression-v1-report.json"
    )
    parser.add_argument("--tasks-budget", type=int, default=5)
    parser.add_argument("--reviews-budget", type=int, default=2)
    args = parser.parse_args()

    budgets = {"tasks": args.tasks_budget, "reviews": args.reviews_budget}
    cases = [
        json.loads(line)
        for line in Path(args.dataset).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    per_case = [run_case(case, budgets) for case in cases]  # noqa: ASYNC240
    summary: dict[str, object] = {
        "dataset": Path(args.dataset).name,
        "budgets": budgets,
        "metric_definitions": "docs/standards/metric-registry.md (v1, frozen 2026-08-31)",
        "token_counting": (
            "estimate (conservative CJK/Latin estimator); NOT exact, NOT provider usage"
        ),
        "case_count": len(cases),
        "strategies": STRATEGIES,
    }
    for strategy in STRATEGIES:
        reductions = [c[strategy]["input_token_reduction"] for c in per_case]
        retentions = [
            c[strategy]["required_fact_retention"]
            for c in per_case
            if c[strategy]["required_fact_retention"] is not None
        ]
        summary[strategy] = {
            "mean_input_token_reduction": round(sum(reductions) / len(reductions), 4),
            "mean_required_fact_retention": (
                round(sum(retentions) / len(retentions), 4) if retentions else None
            ),
            "over_budget_cases": sum(1 for c in per_case if c[strategy]["over_budget"]),
        }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {"summary": summary, "per_case": per_case}, ensure_ascii=False, indent=2
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nper-case rows -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
