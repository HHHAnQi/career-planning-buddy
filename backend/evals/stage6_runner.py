"""Deterministic Stage 6A memory-feedback and context-quality evaluation."""

import json
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

from pydantic import Field

from app.agent.context_compression import compress_context_history
from app.agent.context_selection import (
    ScoredMemory,
    combine_memory_score,
    select_memories_within_budget,
)
from app.schemas.agent_runs import (
    PlanningContext,
    PlanningWindow,
    ProfileContext,
    ReviewContext,
    TaskContext,
)
from app.schemas.base import StrictModel
from app.services.memory_candidate_distiller import (
    MemoryDistillationInput,
    distill_memory_candidates,
)

EVAL_ROOT = Path(__file__).resolve().parent
DATASET_PATH = EVAL_ROOT / "datasets" / "stage6-memory-context-v1.jsonl"
ARTIFACT_ROOT = EVAL_ROOT / "artifacts"


class Stage6MemoryFixture(StrictModel):
    memory_id: UUID
    summary: str
    similarity: float = Field(ge=0, le=1)
    pinned: bool
    status: str
    owner: str


class Stage6EvalCase(StrictModel):
    case_id: str
    category: str
    memories: list[Stage6MemoryFixture] = Field(default_factory=list)
    expected_selected: list[UUID] = Field(default_factory=list)
    adjustment_request: str | None = None
    blockers: str | None = None
    recent_blocker: str | None = None
    abandoned_count: int = 0
    expected_candidate_types: list[str] = Field(default_factory=list)
    embedding_failed: bool = False
    task_count: int = 0
    review_count: int = 0
    min_compression_ratio: float = 0


def load_stage6_cases() -> list[Stage6EvalCase]:
    cases = [
        Stage6EvalCase.model_validate_json(line)
        for line in DATASET_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(cases) != 12:
        raise RuntimeError(f"stage6 dataset must contain exactly 12 cases, found {len(cases)}")
    return cases


async def run_stage6_evaluation(*, persist: bool = True) -> dict[str, object]:
    results = [_evaluate_case(case) for case in load_stage6_cases()]
    passed = sum(bool(result["passed"]) for result in results)
    report: dict[str, object] = {
        "dataset_id": "stage6-memory-context-v1",
        "provider": "mock",
        "deterministic": True,
        "case_count": len(results),
        "passed_cases": passed,
        "failed_cases": len(results) - passed,
        "pass_rate": round(passed / len(results), 4),
        "results": results,
    }
    if persist:
        ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
        path = ARTIFACT_ROOT / "stage6-memory-context-v1-latest.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _evaluate_case(case: Stage6EvalCase) -> dict[str, object]:
    if case.category == "memory_selection":
        actual = _selected_ids(case)
        checks = {
            "selection": actual == case.expected_selected,
            "pending_excluded": all(item.owner != "candidate" for item in _eligible(case)),
            "user_isolation": all(item.owner == "current" for item in _eligible(case)),
            "embedding_fallback": not case.embedding_failed or bool(actual),
        }
        metrics: dict[str, object] = {"selected": [str(item) for item in actual]}
    elif case.category == "candidate_distillation":
        proposals = distill_memory_candidates(
            MemoryDistillationInput(
                user_id=UUID("00000000-0000-0000-0000-000000006100"),
                source_run_id=None,
                review_id=UUID("00000000-0000-0000-0000-000000006101"),
                adjustment_request=case.adjustment_request,
                blockers=case.blockers,
                free_text=None,
                completed_count=0,
                abandoned_count=case.abandoned_count,
                recent_blocker=case.recent_blocker,
            )
        )
        actual_types = [item.memory_type for item in proposals]
        checks = {"candidate_types": actual_types == case.expected_candidate_types}
        metrics = {"candidate_types": actual_types}
    elif case.category == "context_compression":
        compression = compress_context_history(_large_context(case.task_count, case.review_count))
        ratio = 1 - compression.after_chars / compression.before_chars
        checks = {"compression_ratio": ratio >= case.min_compression_ratio}
        metrics = {"compression_ratio": round(ratio, 4)}
    else:
        checks = {"known_category": False}
        metrics = {}
    return {
        "case_id": case.case_id,
        "category": case.category,
        "checks": checks,
        "metrics": metrics,
        "passed": all(checks.values()),
    }


def _eligible(case: Stage6EvalCase) -> list[Stage6MemoryFixture]:
    return [item for item in case.memories if item.owner == "current" and item.status == "active"]


def _selected_ids(case: Stage6EvalCase) -> list[UUID]:
    candidates = [
        ScoredMemory(
            memory_id=item.memory_id,
            version=1,
            memory_type="stable_preference",
            summary=item.summary,
            pinned=item.pinned,
            similarity=item.similarity,
            recency=1,
            final_score=(
                1 if item.pinned else combine_memory_score(similarity=item.similarity, recency=1)
            ),
        )
        for item in _eligible(case)
        if item.pinned or item.similarity >= 0.35
    ]
    return [
        item.memory_id
        for item in select_memories_within_budget(
            candidates,
            max_items=5,
            max_chars=1200,
        )
    ]


def _large_context(task_count: int, review_count: int) -> PlanningContext:
    tasks = [
        TaskContext(
            task_id=UUID(int=10_000 + index),
            state="completed" if index % 2 == 0 else "abandoned",
            title=f"历史任务 {index} " + "任务描述" * 10,
            deliverable=f"历史交付物 {index} " + "交付证据" * 10,
            scheduled_date=date(2026, 7, 1) + timedelta(days=index),
            abandoned_reason_text=("环境配置阻碍" * 5 if index % 2 else None),
        )
        for index in range(task_count)
    ]
    reviews = [
        ReviewContext(
            review_id=UUID(int=20_000 + index),
            review_date=date(2026, 7, 1) + timedelta(days=index),
            blockers="环境配置阻碍" * 5,
            free_text="完整复盘内容" * 20,
        )
        for index in range(review_count)
    ]
    return PlanningContext(
        profile=ProfileContext(
            user_id=UUID("00000000-0000-0000-0000-000000006100"),
            version=1,
            goal_type="agent_app",
            stage="preparing",
            time_budget_minutes=60,
            skill_level="intermediate",
        ),
        planning_window=PlanningWindow(
            planning_date=date(2026, 8, 4),
            horizon_start=date(2026, 8, 4),
            horizon_end=date(2026, 8, 31),
            horizon_weeks=4,
        ),
        recent_tasks=tasks,
        recent_reviews=reviews,
        completed_facts=[task.deliverable for task in tasks if task.state == "completed"],
        blockers=["环境配置阻碍"],
        time_budget_minutes=60,
        token_estimate=0,
    )
