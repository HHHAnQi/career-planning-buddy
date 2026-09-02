"""Build the rubric annotation worksheet from the frozen stage5 dataset.

Runs every plan-producing case (create/repair/replan) through the
configured PlanningProvider (Mock by default — deterministic worksheet;
real provider via env for annotating real model outputs), then writes
``evals/annotations/rubric-v1-worksheet.jsonl`` with an empty
``annotations`` block for the human annotator.

The annotated file is the golden set and belongs in version control.
Regenerating without ``--overwrite`` is refused so annotations are never
lost.

Usage::

    python -m scripts.build_rubric_annotation_set              # mock
    LLM_PROVIDER=openai_compatible ... python -m scripts.build_rubric_annotation_set
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from app.agent.nodes import build_planning_context, fallback_candidate
from app.core.config import get_settings
from app.providers.llm import build_planning_provider
from app.schemas.agent_runs import PlanCandidate, ProviderPlanResponse
from evals.runner import (
    EvalProfile,
    _profile,
    _source_plan,
    _source_review,
    load_cases,
)

WORKSHEET_PATH = Path("evals/annotations/rubric-v1-worksheet.jsonl")
DEFAULT_PROFILE = EvalProfile(
    goal_type="job_search",
    stage="preparing",
    time_budget_minutes=120,
    skill_level="intermediate",
)


async def _candidate_for(case: object) -> PlanCandidate:
    settings = get_settings()
    provider = build_planning_provider(settings)
    profile = _profile(case.profile if case.profile is not None else DEFAULT_PROFILE)
    source_plan = _source_plan(profile) if case.hint_intent == "replan" else None
    source_review = _source_review(case.replan_mode) if source_plan else None
    context = build_planning_context(
        profile=profile,
        requested_horizon_weeks=1,
        source_plan_id=source_plan.plan_id if source_plan else None,
        source_plan_version=source_plan.version if source_plan else None,
        source_plan=source_plan,
        source_review=source_review,
        completed_facts=[],
        planning_date=date(2026, 8, 1),
    )
    raw = await provider.generate_plan(
        message=case.message,
        context=context,
        replan_mode=case.replan_mode or "initial",
        evidence_catalog=[],
    )
    try:
        return ProviderPlanResponse.model_validate(raw).candidate
    except ValidationError:
        # Repair cases deliberately emit an invalid first output; mirror
        # the production pipeline: one repair, then deterministic fallback.
        try:
            repaired = await provider.repair_format(
                raw_output=raw,
                context=context,
                replan_mode=case.replan_mode or "initial",
                evidence_catalog=[],
            )
            return ProviderPlanResponse.model_validate(repaired).candidate
        except ValidationError:
            return fallback_candidate(context, case.replan_mode or "initial")


async def build_rows() -> list[dict[str, object]]:
    settings = get_settings()
    cases = [case for case in load_cases() if case.expected_result_kind == "plan"]
    rows: list[dict[str, object]] = []
    for case in cases:
        candidate = await _candidate_for(case)
        rows.append(
            {
                "case_id": case.case_id,
                "rubric_version": "plan-quality-rubric-v1",
                "provider": settings.llm_provider,
                "request_message": case.message,
                "profile_summary": (
                    f"{case.profile.goal_type if case.profile else 'job_search'}"
                    f" / {case.profile.stage if case.profile else 'preparing'}"
                    f" / {case.profile.skill_level if case.profile else 'intermediate'}"
                    f" / {case.profile.time_budget_minutes if case.profile else 120}min/day"
                ),
                "time_budget_minutes": (
                    case.profile.time_budget_minutes if case.profile else 120
                ),
                "evidence_catalog_ids": [],
                "candidate": candidate.model_dump(mode="json"),
                "annotations": None,
            }
        )

    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overwrite", action="store_true")
    overwrite = parser.parse_args().overwrite
    rows = asyncio.run(build_rows())
    provider_name = str(rows[0]["provider"]) if rows else "unknown"
    WORKSHEET_PATH.parent.mkdir(parents=True, exist_ok=True)
    if WORKSHEET_PATH.exists() and not overwrite:
        raise SystemExit(
            f"{WORKSHEET_PATH} exists; refusing to overwrite annotations "
            "(pass --overwrite to regenerate from scratch)"
        )
    with WORKSHEET_PATH.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(
        f"wrote {len(rows)} rows to {WORKSHEET_PATH} (provider={provider_name})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
