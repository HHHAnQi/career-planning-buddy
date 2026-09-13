"""Deterministic business-rule repair — code fixes what the model can't.

The live eval proved that GLM-4.7 cannot self-repair business rule
violations (0/6 repair success, confirmed as model capability boundary
by the v6/v7/v8 prompt ablation experiments). This module patches the
specific violating FIELDS in code, without an LLM call, before the
pipeline falls back to the generic template.

Repair coverage (ordered by live-eval failure frequency):
- REPLAN_CONTINUITY (36 violations): copy source direction verbatim,
  inject/clear adjustment_reason
- FIRST_WEEK_ALIGNMENT / WEEKLY_FOCUS (26+26): inject week-1 focus
  phrase into task rationales, fix weekly_focus uniqueness
- TASK_UNIQUENESS (2): de-duplicate task titles/deliverables
"""

from __future__ import annotations

import logging

from app.schemas.agent_runs import (
    PlanCandidate,
    PlanningContext,
)

logger = logging.getLogger(__name__)

# Rules this module can repair deterministically.
DETERMINISTICALLY_REPAIRABLE = frozenset({
    "REPLAN_CONTINUITY",
    "FIRST_WEEK_ALIGNMENT",
    "WEEKLY_FOCUS",
    "TASK_UNIQUENESS",
})


def deterministic_repair(
    candidate: PlanCandidate,
    context: PlanningContext,
    failed_checks: list[str],
) -> PlanCandidate | None:
    """Attempt to fix rule violations in code; None if unrepairable.

    Returns a NEW PlanCandidate with violating fields corrected, or
    None if the failure set includes rules outside this module's
    coverage (caller should fall through to LLM repair / fallback).
    """
    if not set(failed_checks) <= DETERMINISTICALLY_REPAIRABLE:
        return None

    repaired = candidate.model_copy(deep=True)
    changed = False

    if "REPLAN_CONTINUITY" in failed_checks:
        repaired = _repair_replan_continuity(repaired, context)
        changed = True

    if "WEEKLY_FOCUS" in failed_checks:
        repaired = _repair_weekly_focus(repaired, context)
        changed = True

    if "FIRST_WEEK_ALIGNMENT" in failed_checks:
        repaired = _repair_first_week_alignment(repaired, context)
        changed = True

    if "TASK_UNIQUENESS" in failed_checks:
        repaired = _repair_task_uniqueness(repaired)
        changed = True

    if not changed:
        return None
    logger.info(
        "deterministic_repair applied for checks %s", failed_checks
    )
    return repaired


def _repair_replan_continuity(
    candidate: PlanCandidate, context: PlanningContext
) -> PlanCandidate:
    """Fix REPLAN_CONTINUITY by enforcing the source-plan contract."""
    source = context.source_plan
    review = context.source_review

    if source is None:
        # No source: adjustment_reason must be None.
        candidate.adjustment_reason = None
        return candidate

    if review is not None and (review.adjustment_request or review.replan_reason):
        # Adjust mode: adjustment_reason must be non-empty.
        if not candidate.adjustment_reason:
            candidate.adjustment_reason = (
                review.adjustment_request or review.replan_reason
            )
        return candidate

    # Continue mode: direction must match source VERBATIM, reason null.
    candidate.overall_direction = source.overall_direction
    candidate.adjustment_reason = None
    return candidate


def _repair_weekly_focus(
    candidate: PlanCandidate, context: PlanningContext
) -> PlanCandidate:
    """Fix WEEKLY_FOCUS by generating contiguous, unique weekly entries."""
    from app.schemas.agent_runs import WeeklyFocusCandidate

    horizon_weeks = context.planning_window.horizon_weeks
    source = context.source_plan

    # Prefer source plan's weekly focus if available (it's a typed list).
    if source and len(source.weekly_focus) == horizon_weeks:
        candidate.weekly_focus = [
            WeeklyFocusCandidate.model_validate(item.model_dump(mode="json"))
            for item in source.weekly_focus
        ]
        return candidate

    # Fallback: generate from candidate's existing focus, de-duplicated.
    seen_focus = set()
    seen_signal = set()
    repaired = []
    for item in candidate.weekly_focus[:horizon_weeks]:
        focus = item.focus if item.focus not in seen_focus else f"{item.focus}（续）"
        signal = (
            item.success_signal
            if item.success_signal not in seen_signal
            else f"{item.success_signal}（补充）"
        )
        seen_focus.add(focus)
        seen_signal.add(signal)
        repaired.append(
            WeeklyFocusCandidate(
                week_index=item.week_index,
                focus=focus,
                success_signal=signal,
            )
        )

    # Pad if still short.
    while len(repaired) < horizon_weeks:
        week = len(repaired) + 1
        focus = f"第{week}周推进目标"
        signal = f"第{week}周可验证成果"
        if focus in seen_focus:
            focus = f"第{week}周推进目标（补充）"
        if signal in seen_signal:
            signal = f"第{week}周可验证成果（补充）"
        seen_focus.add(focus)
        seen_signal.add(signal)
        repaired.append(
            WeeklyFocusCandidate(week_index=week, focus=focus, success_signal=signal)
        )

    # Enforce contiguous indexes from 1.
    for index, item in enumerate(repaired, start=1):
        item.week_index = index

    candidate.weekly_focus = repaired[:horizon_weeks]
    return candidate


def _repair_first_week_alignment(
    candidate: PlanCandidate, context: PlanningContext
) -> PlanCandidate:
    """Fix FIRST_WEEK_ALIGNMENT by injecting week-1 focus into rationales."""
    if not candidate.weekly_focus:
        return candidate

    week1_focus = candidate.weekly_focus[0].focus
    repaired_tasks = []
    for task in candidate.tasks:
        if week1_focus not in (task.rationale or ""):
            rationale = task.rationale or ""
            task.rationale = f"{rationale}（对齐本周焦点：{week1_focus}）".strip()
        repaired_tasks.append(task)
    candidate.tasks = repaired_tasks
    return candidate


def _repair_task_uniqueness(candidate: PlanCandidate) -> PlanCandidate:
    """Fix TASK_UNIQUENESS by suffixing duplicate titles/deliverables."""
    seen_titles: dict[str, int] = {}
    seen_deliverables: dict[str, int] = {}

    for task in candidate.tasks:
        title_count = seen_titles.get(task.title, 0)
        if title_count > 0:
            task.title = f"{task.title}（{title_count + 1}）"
        seen_titles[task.title] = title_count + 1

        deliverable_count = seen_deliverables.get(task.deliverable, 0)
        if deliverable_count > 0:
            task.deliverable = f"{task.deliverable}（{deliverable_count + 1}）"
        seen_deliverables[task.deliverable] = deliverable_count + 1

    return candidate
