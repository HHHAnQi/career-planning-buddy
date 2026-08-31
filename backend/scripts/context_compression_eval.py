"""Offline context-compression comparison v2 (deterministic, zero calls).

Corrections over v1 (whose report is retained with caveats):

  * model-input scoring ONLY — retention is judged on the text windows
    the model actually receives (retained records + summary lines);
    summary_sources is provenance, never scoring input;
  * newest-first retention contract (compression sorts internally);
  * component-based fact scoring (numbers / status / anchors / coverage)
    with an explicit needs_review bucket — never counted as retained;
  * recent and relevant_summary optimize the SAME final input budget
    (summaries participate in the shrink loop);
  * over-budget results reported, never dropped.

Offline results validate BRANCH LOGIC on synthetic histories only — the
relevance arm runs without real embeddings here, so no claim about true
semantic recall is made.
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
from app.prompts.career_planning import generation_messages
from app.schemas.agent_runs import (
    PlanningContext,
    ProfileContext,
    ReviewContext,
    TaskContext,
)
from app.schemas.enums import (
    CareerStage,
    GoalType,
    ReplanMode,
    SkillLevel,
    TaskStatus,
)
from evals.context_metrics import score_fact

DATASET = (
    Path(__file__).resolve().parents[1] / "evals/datasets/context-compression-v1.jsonl"
)
STRATEGIES = ("full", "recent", "relevant_summary")
PLANNING_DATE = date(2026, 8, 31)


def _profile(case: dict) -> ProfileContext:
    raw = case["profile"]
    return ProfileContext(
        user_id=uuid4(),
        version=1,
        goal_type=GoalType.AI_BACKEND,
        stage=CareerStage.PREPARING,
        time_budget_minutes=raw.get("time_budget_minutes", 90),
        skill_level=SkillLevel.INTERMEDIATE,
    )


def _context(case: dict) -> PlanningContext:
    from app.agent.nodes import build_planning_context

    tasks = [
        TaskContext(
            task_id=uuid4(),
            state=(
                TaskStatus.COMPLETED
                if t["state"] == "completed"
                else TaskStatus.ABANDONED
            ),
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
    completed = [t.deliverable for t in tasks if t.state == TaskStatus.COMPLETED]
    blockers = [
        t.abandoned_reason_text or t.deliverable
        for t in tasks
        if t.state == TaskStatus.ABANDONED
    ]
    return build_planning_context(
        profile=_profile(case),
        requested_horizon_weeks=None,
        source_plan_id=None,
        source_plan_version=None,
        completed_facts=completed,
        blockers=blockers,
        planning_date=PLANNING_DATE,
    ).model_copy(update={"recent_tasks": tasks, "recent_reviews": reviews})


def _rendered_clauses(messages: list[dict[str, str]]) -> list[str]:
    """Clauses of the ACTUAL rendered request the model receives — the
    only legitimate scoring evidence. Reconstructed context fields and
    provenance lists are never used for scoring."""
    from evals.context_metrics import _split_clauses

    body = "".join(
        m["content"] for m in messages if m.get("role") != "system"
    )
    return _split_clauses(body)


def _compress(context, budgets, request, strategy, max_tokens):
    return compress_context_history(
        context,
        recent_tasks_budget=budgets["tasks"],
        recent_reviews_budget=budgets["reviews"],
        focus_query=request,
        max_context_tokens=max_tokens,
        strategy=strategy,
    )


def run_case(case: dict, budgets: dict[str, int]) -> dict[str, object]:
    context = _context(case)
    request = case["request"]
    annotations = case["annotations"]
    required = annotations["required_facts"]
    max_tokens = case.get("max_context_tokens")

    rendered_full = generation_messages(
        message=request, context=context, replan_mode=ReplanMode.CONTINUE
    )
    full_tokens = estimate_text_tokens(
        "".join(m["content"] for m in rendered_full)
    )

    rows: dict[str, object] = {
        "case_id": case["case_id"],
        "scenario": case["scenario"],
    }
    for strategy in STRATEGIES:
        result = _compress(context, budgets, request, strategy, max_tokens)
        rendered = generation_messages(
            message=request,
            context=result.context,
            replan_mode=ReplanMode.CONTINUE,
        )
        tokens = estimate_text_tokens("".join(m["content"] for m in rendered))
        windows = _rendered_clauses(rendered)
        # Final-request over-limit (context + system + schema) measured
        # on the same rendered messages with the per-section estimator.
        from app.prompts.career_planning import rendered_input_estimate

        final_estimate = rendered_input_estimate(rendered)
        fact_rows = []
        verdicts = []
        for fact in required:
            verdict, detail = score_fact(fact, windows, request_text=request)
            verdicts.append(verdict)
            fact_rows.append({"fact": fact, "verdict": verdict, "detail": detail})
        retained = verdicts.count("retained")
        needs_review = verdicts.count("needs_review")
        rows[strategy] = {
            "estimated_context_tokens": tokens,
            "estimated_final_request_tokens": final_estimate[
                "estimate_total_tokens"
            ],
            "context_over_budget": bool(
                max_tokens is not None and tokens > max_tokens
            ),
            "final_request_over_budget": bool(
                max_tokens is not None
                and final_estimate["estimate_total_tokens"] > max_tokens
            ),
            "input_token_reduction": (
                round((full_tokens - tokens) / full_tokens, 4)
                if full_tokens
                else 0.0
            ),
            "facts_total": len(required),
            "facts_retained": retained,
            "facts_needs_review": needs_review,
            "facts_lost": len(required) - retained - needs_review,
            "fact_retention_macro": (
                round(retained / len(required), 4) if required else None
            ),
            "fact_details": fact_rows,
            "over_budget": result.over_budget,
            "budget_shrink_steps": result.budget_shrink_steps,
            "promoted_task_count": result.promoted_task_count,
            "pruned": [
                {"reason": p.reason, "deliverable": p.original_deliverable}
                for p in result.pruned
            ],
        }

    # Branch assertion (case-level): for the out-of-window-relevant
    # scenario the relevance arm must engage rescue OR keep the fact via
    # its summary — otherwise the "relevance" label is untested.
    if case["scenario"].startswith("old_but_relevant"):
        rel = rows["relevant_summary"]
        rows["branch_check"] = {
            "scenario": case["scenario"],
            "relevant_arm_promoted": rel["promoted_task_count"] >= 1,
            "relevant_arm_keeps_fact_via_window_or_summary": any(
                f["verdict"] == "retained" for f in rel["fact_details"]
            ),
        }
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
        "--out", default="evals/artifacts/context-compression-v3-report.json"
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
    per_case = [run_case(case, budgets) for case in cases]

    summary: dict[str, object] = {
        "report_version": "v3",
        "v2_caveat": (
            "v2 scored against reconstructed context-field windows; v3 "
            "scores against clauses of the ACTUAL rendered request, uses "
            "exact digit-run sets (no substring matches), and judges "
            "status/negation clause-locally."
        ),
        "v1_caveat": (
            "v1 (context-compression-v1-report.json) scored retention "
            "against text the model never received (summary_sources) "
            "under an unspecified ordering contract; its retention "
            "numbers are invalid, retained for the record only."
        ),
        "dataset": Path(args.dataset).name,
        "budgets": budgets,
        "budget_semantics": (
            "recent and relevant_summary optimize the SAME final input "
            "budget (summaries included in the shrink loop)"
        ),
        "token_counting": (
            "estimate (conservative CJK/Latin heuristic); NOT exact "
            "tokenizer; NOT provider usage"
        ),
        "scoring_rule": "evals/context_metrics.py (frozen v2, pre-registered)",
        "case_count": len(cases),
        "offline_limitation": (
            "no real embeddings in the offline relevance arm — results "
            "validate branch logic only, not semantic recall quality"
        ),
    }
    for strategy in STRATEGIES:
        rows = [c[strategy] for c in per_case]
        sendable = [
            r for r in rows if not r.get("final_request_over_budget")
        ]

        def _block(subset: list, label: str) -> dict[str, object]:
            reductions = [r["input_token_reduction"] for r in subset]
            total = sum(r["facts_total"] for r in subset)
            retained = sum(r["facts_retained"] for r in subset)
            needs_review = sum(r["facts_needs_review"] for r in subset)
            macro = [
                r["fact_retention_macro"]
                for r in subset
                if r["fact_retention_macro"] is not None
            ]
            return {
                "cases": len(subset),
                "mean_input_token_reduction": (
                    round(sum(reductions) / len(reductions), 4)
                    if reductions
                    else None
                ),
                "fact_retention_macro_mean": (
                    round(sum(macro) / len(macro), 4) if macro else None
                ),
                "fact_retention_micro": (
                    {
                        "retained": retained,
                        "needs_review": needs_review,
                        "lost": total - retained - needs_review,
                        "total": total,
                        "denominator": f"{label}: all annotated facts",
                    }
                    if total
                    else None
                ),
            }

        summary[strategy] = {
            "all_samples": _block(rows, "all samples"),
            "sendable_samples": _block(
                sendable, "sendable samples (final request within budget)"
            ),
            "context_over_budget_cases": sum(
                1 for r in rows if r.get("context_over_budget")
            ),
            "final_request_over_budget_cases": sum(
                1 for r in rows if r.get("final_request_over_budget")
            ),
        }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {"summary": summary, "per_case": per_case},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nper-case rows -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
