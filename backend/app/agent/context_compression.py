"""Deterministic history compression: relevance rescue + dynamic token budget.

Three-stage pipeline, all deterministic (no LLM call):

1. Relevance rescue — older tasks are scored by character-bigram overlap
   with the current request; high-relevance older tasks are promoted into
   the retained window instead of being folded into a summary. Recency
   alone loses "the relevant thing happened 3 weeks ago" cases.
2. Dynamic budget — the recency window shrinks stepwise (down to a floor)
   while the serialized context exceeds the per-call input budget, so
   compression adapts to history size instead of a fixed window.
3. Summary folding — everything not retained is compressed into structured
   summaries (completed deliverables, repeated blockers, adjustment
   patterns), never silently dropped.
"""

from dataclasses import dataclass
from math import ceil

from app.schemas.agent_runs import PlanningContext, ReviewContext, TaskContext

_RELEVANCE_RESCUE_LIMIT = 2
_RELEVANCE_RESCUE_THRESHOLD = 0.08
_MIN_RETAINED_TASKS = 2
_MIN_RETAINED_REVIEWS = 1


@dataclass(frozen=True, slots=True)
class ContextCompressionResult:
    context: PlanningContext
    before_chars: int
    after_chars: int
    task_compressed_count: int
    review_compressed_count: int
    promoted_task_count: int = 0
    budget_shrink_steps: int = 0


def estimate_text_tokens(text: str) -> int:
    """Conservatively estimate mixed Chinese/Latin text without a Provider tokenizer."""
    if not text:
        return 0
    non_ascii = sum(ord(character) > 127 for character in text)
    ascii_chars = len(text) - non_ascii
    return non_ascii + ceil(ascii_chars / 4)


def _bigrams(text: str) -> set[str]:
    normalized = "".join(text.split()).lower()
    if len(normalized) < 2:
        return {normalized} if normalized else set()
    return {normalized[i : i + 2] for i in range(len(normalized) - 1)}


def _task_relevance(focus_bigrams: set[str], task: TaskContext) -> float:
    """Overlap ratio between the request and a task's planning surface."""
    surface = " ".join(
        part
        for part in (task.deliverable, task.abandoned_reason_text or "")
        if part
    )
    task_bigrams = _bigrams(surface)
    if not focus_bigrams or not task_bigrams:
        return 0.0
    return len(focus_bigrams & task_bigrams) / len(focus_bigrams)


def _select_retained_tasks(
    tasks: list[TaskContext],
    budget: int,
    focus_query: str | None,
) -> tuple[list[TaskContext], list[TaskContext], int]:
    """Recency window + bounded relevance rescue from older tasks."""
    recent = tasks[:budget]
    older = tasks[budget:]
    if not older or not focus_query:
        return recent, older, 0
    focus_bigrams = _bigrams(focus_query)
    ranked = sorted(
        enumerate(older),
        key=lambda pair: (-_task_relevance(focus_bigrams, pair[1]), pair[0]),
    )
    rescued: list[tuple[int, TaskContext]] = []
    for index, task in ranked[:_RELEVANCE_RESCUE_LIMIT]:
        if _task_relevance(focus_bigrams, task) >= _RELEVANCE_RESCUE_THRESHOLD:
            rescued.append((index, task))
    if not rescued:
        return recent, older, 0
    rescued_indices = {index for index, _ in rescued}
    # Rescued tasks join the retained window in original chronological order
    # so downstream consumers still see a stable, ordered history.
    retained = list(recent) + [task for _, task in rescued]
    remaining_older = [task for i, task in enumerate(older) if i not in rescued_indices]
    return retained, remaining_older, len(rescued)


def compress_context_history(
    context: PlanningContext,
    *,
    recent_tasks_budget: int = 5,
    recent_reviews_budget: int = 2,
    focus_query: str | None = None,
    max_context_tokens: int | None = None,
) -> ContextCompressionResult:
    """Compress history.

    PR-8 exposes the per-context budget as kwargs. Pre-PR-8 callers see the
    same defaults (5 tasks / 2 reviews) and identical behaviour when the
    new relevance/budget knobs are omitted.
    """
    before_chars = len(context.model_dump_json())
    retained_tasks, older_tasks, promoted = _select_retained_tasks(
        context.recent_tasks, recent_tasks_budget, focus_query
    )
    retained_reviews = context.recent_reviews[:recent_reviews_budget]
    older_reviews = context.recent_reviews[recent_reviews_budget:]

    shrink_steps = 0
    if max_context_tokens is not None:
        # Dynamic budget: shed recency headroom (never below the floor)
        # until the serialized context fits the per-call input budget.
        while (
            estimate_text_tokens(_preview(context, retained_tasks, retained_reviews))
            > max_context_tokens
            and (
                len(retained_tasks) > _MIN_RETAINED_TASKS
                or len(retained_reviews) > _MIN_RETAINED_REVIEWS
            )
        ):
            if len(retained_tasks) > _MIN_RETAINED_TASKS:
                older_tasks = [retained_tasks.pop()] + older_tasks
            if len(retained_reviews) > _MIN_RETAINED_REVIEWS:
                older_reviews = [retained_reviews.pop()] + older_reviews
            shrink_steps += 1

    task_summary = _task_summary(older_tasks)
    review_summary = _review_summary(older_reviews)
    summarized_deliverables = {task.deliverable.strip() for task in context.recent_tasks}
    completed_facts = [
        fact
        for fact in dict.fromkeys(context.completed_facts)
        if fact.strip() not in summarized_deliverables
    ][:5]
    compressed = context.model_copy(
        update={
            "recent_tasks": retained_tasks,
            "recent_reviews": retained_reviews,
            "completed_facts": completed_facts,
            "task_history_summary": task_summary,
            "review_history_summary": review_summary,
            "token_estimate": 0,
        }
    )
    token_estimate = estimate_text_tokens(compressed.model_dump_json())
    compressed = compressed.model_copy(update={"token_estimate": token_estimate})
    final_estimate = estimate_text_tokens(compressed.model_dump_json())
    if final_estimate != token_estimate:
        compressed = compressed.model_copy(update={"token_estimate": final_estimate})
    return ContextCompressionResult(
        context=compressed,
        before_chars=before_chars,
        after_chars=len(compressed.model_dump_json()),
        task_compressed_count=len(context.recent_tasks) - len(retained_tasks),
        review_compressed_count=len(context.recent_reviews) - len(retained_reviews),
        promoted_task_count=promoted,
        budget_shrink_steps=shrink_steps,
    )


def _preview(
    context: PlanningContext,
    retained_tasks: list[TaskContext],
    retained_reviews: list[ReviewContext],
) -> str:
    preview = context.model_copy(
        update={
            "recent_tasks": retained_tasks,
            "recent_reviews": retained_reviews,
            "task_history_summary": None,
            "review_history_summary": None,
            "token_estimate": 0,
        }
    )
    return preview.model_dump_json()


def _task_summary(tasks: list[TaskContext]) -> str | None:
    completed = list(
        dict.fromkeys(task.deliverable.strip() for task in tasks if task.state == "completed")
    )[:5]
    blockers = list(
        dict.fromkeys(
            (task.abandoned_reason_text or task.deliverable).strip()
            for task in tasks
            if task.state == "abandoned"
        )
    )[:3]
    parts: list[str] = []
    if completed:
        parts.append("更早任务已完成：" + "、".join(completed))
    if blockers:
        parts.append("主要阻碍：" + "、".join(blockers))
    return "；".join(parts) or None


def _review_summary(reviews: list[ReviewContext]) -> str | None:
    blocker_counts: dict[str, int] = {}
    adjustments: list[str] = []
    for review in reviews:
        if review.blockers and review.blockers.strip():
            blocker = review.blockers.strip()
            blocker_counts[blocker] = blocker_counts.get(blocker, 0) + 1
        if review.adjustment_request and review.adjustment_request.strip():
            adjustments.append(review.adjustment_request.strip())
    repeated = [value for value, count in blocker_counts.items() if count >= 2][:3]
    parts: list[str] = []
    if repeated:
        parts.append("重复阻碍：" + "、".join(repeated))
    unique_adjustments = list(dict.fromkeys(adjustments))[:3]
    if unique_adjustments:
        parts.append("调整模式：" + "、".join(unique_adjustments))
    return "；".join(parts) or None
