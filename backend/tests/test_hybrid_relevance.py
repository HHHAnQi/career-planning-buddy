"""Hybrid relevance scoring: synonym rewrites must rank above pure-lexical.

修复8 acceptance: bigram-only scoring gives a synonym-rewritten task
(跳槽 vs 换工作) a near-zero score; adding the embedding cosine half
(mimicked here with a stub provider mapping synonyms to identical
vectors) must rescue it into the retained window while unrelated tasks
stay below the threshold.
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from app.agent.context_compression import _select_retained_tasks, _task_relevance
from app.schemas.agent_runs import TaskContext, TaskStatus


def _task(deliverable: str) -> TaskContext:
    return TaskContext(
        task_id=uuid4(),
        state=TaskStatus.COMPLETED,
        title="",
        deliverable=deliverable,
        scheduled_date=date(2026, 8, 1),
    )


SYNONYM_TASK = _task("完成跳槽准备：整理目标公司清单")
UNRELATED_TASK = _task("整理房间杂物")
QUERY = "帮我基于上次换工作的准备情况做新计划"

# Stub embedding space: one axis per semantic cluster.
AXIS_JUMP = [1.0, 0.0]
AXIS_HOUSE = [0.0, 1.0]
QUERY_VECTOR = AXIS_JUMP
TASK_VECTORS = {0: AXIS_JUMP, 1: AXIS_HOUSE}


def test_lexical_only_misses_the_synonym_rewrite() -> None:
    score = _task_relevance(set(_bigrams(QUERY)), SYNONYM_TASK)
    assert score < 0.08, f"synonym rewrite scored lexically high: {score}"


def test_hybrid_score_rescues_the_synonym_rewrite() -> None:
    hybrid = _task_relevance(
        set(_bigrams(QUERY)),
        SYNONYM_TASK,
        query_vector=QUERY_VECTOR,
        task_vector=AXIS_JUMP,
    )
    assert hybrid >= 0.5 * 1.0 - 1e-6, hybrid
    unrelated = _task_relevance(
        set(_bigrams(QUERY)),
        UNRELATED_TASK,
        query_vector=QUERY_VECTOR,
        task_vector=AXIS_HOUSE,
    )
    assert unrelated < hybrid


def test_rescue_promotes_semantically_relevant_older_task() -> None:
    recent = [UNRELATED_TASK]
    older = [SYNONYM_TASK]
    retained, remaining, promoted = _select_retained_tasks(
        recent + older,
        budget=1,
        focus_query=QUERY,
        query_vector=QUERY_VECTOR,
        task_vectors={0: AXIS_JUMP},  # index within `older`
    )
    assert promoted == 1
    assert SYNONYM_TASK in retained
    assert remaining == []


def _bigrams(text: str) -> set[str]:
    normalized = "".join(text.split()).lower()
    return {normalized[i : i + 2] for i in range(len(normalized) - 1)}
