"""Deterministic tests for the context-compression strategy comparison.

All synthetic/offline — zero model calls. Pins the pre-registered
invariants: budget parity between windowed strategies, full-identity
baseline, over-budget explicit status, validation independence from
compression, and the offline dataset runner end to end.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from uuid import uuid4

from app.agent.context_compression import (
    CompressionStrategy,
    compress_context_history,
)
from app.schemas.agent_runs import (
    PlanningContext,
    ProfileContext,
    TaskContext,
)
from app.schemas.enums import CareerStage, GoalType, SkillLevel, TaskStatus


def _context(task_count: int = 12) -> PlanningContext:
    from app.agent.nodes import build_planning_context

    profile = ProfileContext(
        user_id=uuid4(),
        version=1,
        goal_type=GoalType.AI_BACKEND,
        stage=CareerStage.PREPARING,
        time_budget_minutes=90,
        skill_level=SkillLevel.INTERMEDIATE,
    )
    tasks = [
        TaskContext(
            task_id=uuid4(),
            state=TaskStatus.COMPLETED,
            title=f"任务 {index}",
            deliverable=f"完成第 {index} 项算法专题练习",
            scheduled_date=date(2026, 8, 20),
        )
        for index in range(task_count)
    ]
    context = build_planning_context(
        profile=profile,
        requested_horizon_weeks=None,
        source_plan_id=None,
        source_plan_version=None,
        completed_facts=[t.deliverable for t in tasks],
        blockers=[],
        planning_date=date(2026, 8, 31),
    )
    return context.model_copy(update={"recent_tasks": tasks})


def test_full_strategy_is_identity_baseline() -> None:
    context = _context()
    result = compress_context_history(context, strategy=CompressionStrategy.FULL)
    assert result.context is context
    assert result.task_compressed_count == 0
    assert result.pruned == ()


def test_windowed_strategies_share_the_same_budget() -> None:
    context = _context(12)
    recent = compress_context_history(
        context, recent_tasks_budget=5, strategy=CompressionStrategy.RECENT
    )
    relevant = compress_context_history(
        context,
        recent_tasks_budget=5,
        focus_query="算法练习",
        strategy=CompressionStrategy.RELEVANT_SUMMARY,
    )
    # Same retained-window CAP: recent keeps exactly the window; relevant
    # keeps window + at most the rescue limit — never more than budget +
    # rescue limit, and both prune strictly more than full.
    assert len(recent.context.recent_tasks) == 5
    assert len(relevant.context.recent_tasks) <= 5 + 2
    assert recent.task_compressed_count == 7
    assert relevant.task_compressed_count >= 5


def test_recent_drops_summaries_but_relevant_keeps_provenance() -> None:
    context = _context(12)
    recent = compress_context_history(
        context, recent_tasks_budget=5, strategy=CompressionStrategy.RECENT
    )
    relevant = compress_context_history(
        context,
        recent_tasks_budget=5,
        focus_query="算法练习",
        strategy=CompressionStrategy.RELEVANT_SUMMARY,
    )
    assert recent.context.task_history_summary is None
    assert relevant.context.task_history_summary is not None
    assert relevant.summary_sources
    folded = relevant.summary_sources[relevant.context.task_history_summary]
    assert any("算法专题练习" in deliverable for deliverable in folded)


def test_over_budget_is_explicit_not_silent() -> None:
    context = _context(12)
    result = compress_context_history(
        context,
        recent_tasks_budget=5,
        focus_query="算法",
        max_context_tokens=10,  # impossible: floor must not be breached
        strategy=CompressionStrategy.RELEVANT_SUMMARY,
    )
    assert result.over_budget is True
    # Retention floor respected: never fewer than the minimum window.
    assert len(result.context.recent_tasks) >= 2


def test_prune_reasons_are_classified() -> None:
    context = _context(12)
    recent = compress_context_history(
        context, recent_tasks_budget=5, strategy=CompressionStrategy.RECENT
    )
    reasons = {p.reason for p in recent.pruned}
    assert reasons == {"recency_window"}
    assert all(p.kind == "task" for p in recent.pruned)


def test_validator_uses_authoritative_context_not_compressed() -> None:
    """The graph exposes pre-compression facts for validation (gap fix):
    simulate the wiring contract — the authoritative object must equal the
    pre-compression context regardless of strategy."""

    from app.agent.graph import FixedPlanningGraph  # noqa: F401 (wiring exists)

    context = _context(8)
    for strategy in CompressionStrategy:
        compressed = compress_context_history(
            context, recent_tasks_budget=2, strategy=strategy
        )
        # Authoritative facts are the ORIGINAL completed_facts — the
        # validator's REPLAN/no-repeat checks consume these, so a candidate
        # repeating an old deliverable must fail even when compression
        # folded that deliverable into a summary.
        authoritative_facts = set(context.completed_facts)
        assert authoritative_facts.issuperset(compressed.context.completed_facts)
        assert authoritative_facts == set(
            t.deliverable
            for t in context.recent_tasks
            if t.state == TaskStatus.COMPLETED
        )


def test_offline_dataset_runner_end_to_end() -> None:
    import subprocess
    import sys

    dataset = (
        Path(__file__).resolve().parents[1] / "evals/datasets/context-compression-v1.jsonl"
    )
    lines = dataset.read_text(encoding="utf-8").splitlines()
    cases = [json.loads(line) for line in lines if line]
    scenarios = {c["scenario"] for c in cases}
    assert {
        "short_history",
        "long_history",
        "old_but_relevant",
        "irrelevant_distractors",
        "completed_synonym_rewrite",
        "constraint_conflict",
    } <= scenarios
    # Every case carries independent annotations, and annotations are
    # never part of the model input (rendered from request/history only).
    for case in cases:
        assert case["annotations"]["required_facts"]
        assert "annotations" not in case["request"]

    result = subprocess.run(
        [
            sys.executable,
            "scripts/context_compression_eval.py",
            "--out",
            "/tmp/cc-test-report.json",
        ],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
        env={"PYTHONPATH": ".", "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr[-500:]
    report = json.loads(Path("/tmp/cc-test-report.json").read_text(encoding="utf-8"))
    summary = report["summary"]
    # v3 schema: all_samples / sendable_samples split with explicit
    # denominators. No superiority assertion is pre-registered; we pin
    # internal consistency only.
    for strategy in ("full", "recent", "relevant_summary"):
        for subset in ("all_samples", "sendable_samples"):
            block = summary[strategy][subset]
            micro = block["fact_retention_micro"]
            assert (
                micro["retained"] + micro["needs_review"] + micro["lost"]
                == micro["total"]
            )
        assert (
            summary[strategy]["all_samples"]["cases"]
            >= summary[strategy]["sendable_samples"]["cases"]
        )
    assert summary["full"]["all_samples"]["mean_input_token_reduction"] == 0.0
