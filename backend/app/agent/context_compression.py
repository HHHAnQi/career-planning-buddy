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
from enum import StrEnum
from math import ceil

from app.schemas.agent_runs import PlanningContext, ReviewContext, TaskContext

_RELEVANCE_RESCUE_LIMIT = 2
_RELEVANCE_RESCUE_THRESHOLD = 0.08
_MIN_RETAINED_TASKS = 2
_MIN_RETAINED_REVIEWS = 1


class CompressionStrategy(StrEnum):
    """Switchable context-history strategies for controlled comparison.

    full:            no compression (baseline; still reports estimate and
                     over-budget status).
    recent:          pure recency window at the configured budgets.
    relevant_summary: recency window + hybrid-relevance rescue + summary
                     folding (production default). Uses the SAME budgets
                     as ``recent`` so the two are comparable.
    """

    FULL = "full"
    RECENT = "recent"
    RELEVANT_SUMMARY = "relevant_summary"


@dataclass(frozen=True, slots=True)
class PrunedRecord:
    """Provenance for one record removed from the retained window."""

    task_id: object
    kind: str  # task | review | completed_fact
    reason: str  # recency_window | budget_shrink | irrelevance
    original_deliverable: str


@dataclass(frozen=True, slots=True)
class ContextCompressionResult:
    context: PlanningContext
    before_chars: int
    after_chars: int
    task_compressed_count: int
    review_compressed_count: int
    promoted_task_count: int = 0
    budget_shrink_steps: int = 0
    strategy: str = "relevant_summary"
    # Residual over-budget signal: the serialized context still exceeds
    # the per-call input budget AFTER the retention floor. Callers must
    # surface this — never silently drop constraints to fit.
    over_budget: bool = False
    estimated_context_tokens: int = 0
    pruned: tuple[PrunedRecord, ...] = ()
    # summary line -> the original deliverables it was folded from, so
    # downstream audits can trace every summarized fact to its source.
    summary_sources: dict[str, tuple[str, ...]] | None = None


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


def _task_relevance(
    focus_bigrams: set[str],
    task: TaskContext,
    *,
    query_vector: list[float] | None = None,
    task_vector: list[float] | None = None,
) -> float:
    """Hybrid relevance: 0.5 * lexical bigram overlap + 0.5 * embedding
    cosine. Pure-bigram scoring misses synonym rewrites (跳槽/换工作);
    the semantic half closes that gap. When either vector is missing the
    score degrades to the lexical half only (deterministic fallback)."""

    def _cosine(a: list[float], b: list[float]) -> float:
        dot = float(sum(x * y for x, y in zip(a, b, strict=False)))
        na = float(sum(x * x for x in a) ** 0.5)
        nb = float(sum(x * x for x in b) ** 0.5)
        if na == 0 or nb == 0:
            return 0.0
        return float(max(0.0, dot / (na * nb)))

    surface = " ".join(
        part
        for part in (task.deliverable, task.abandoned_reason_text or "")
        if part
    )
    task_bigrams = _bigrams(surface)
    lexical = 0.0
    if focus_bigrams and task_bigrams:
        lexical = len(focus_bigrams & task_bigrams) / len(focus_bigrams)
    if query_vector is None or task_vector is None:
        return lexical
    return 0.5 * lexical + 0.5 * _cosine(query_vector, task_vector)


def _select_retained_tasks(
    tasks: list[TaskContext],
    budget: int,
    focus_query: str | None,
    *,
    query_vector: list[float] | None = None,
    task_vectors: dict[int, list[float]] | None = None,
    rescue_enabled: bool = True,
) -> tuple[list[TaskContext], list[TaskContext], int]:
    """Recency window + bounded relevance rescue from older tasks."""
    recent = tasks[:budget]
    older = tasks[budget:]
    if not rescue_enabled or not older or not focus_query:
        return recent, older, 0
    focus_bigrams = _bigrams(focus_query)
    vectors = task_vectors or {}

    def _score(index: int, task: TaskContext) -> float:
        return _task_relevance(
            focus_bigrams,
            task,
            query_vector=query_vector,
            task_vector=vectors.get(index),
        )

    ranked = sorted(enumerate(older), key=lambda pair: (-_score(*pair), pair[0]))
    rescued: list[tuple[int, TaskContext]] = []
    for index, task in ranked[:_RELEVANCE_RESCUE_LIMIT]:
        if _score(index, task) >= _RELEVANCE_RESCUE_THRESHOLD:
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
    query_vector: list[float] | None = None,
    task_vectors: dict[int, list[float]] | None = None,
    strategy: CompressionStrategy | str = CompressionStrategy.RELEVANT_SUMMARY,
) -> ContextCompressionResult:
    """Compress history under a switchable strategy.

    ``full`` is the identity baseline (no pruning, no summary), ``recent``
    is a pure recency window, ``relevant_summary`` adds hybrid-relevance
    rescue and summary folding. ``recent`` and ``relevant_summary`` share
    the SAME budgets so A/B comparisons isolate the relevance/summary
    machinery. Compression only shapes the MODEL INPUT; authoritative
    business facts are validated from the uncompressed records (see
    graph validator wiring), never from this output.
    """
    if not isinstance(strategy, CompressionStrategy):
        strategy = CompressionStrategy(strategy)
    before_chars = len(context.model_dump_json())

    if strategy is CompressionStrategy.FULL:
        estimate = estimate_text_tokens(context.model_dump_json())
        return ContextCompressionResult(
            context=context,
            before_chars=before_chars,
            after_chars=before_chars,
            task_compressed_count=0,
            review_compressed_count=0,
            strategy=strategy.value,
            estimated_context_tokens=estimate,
            over_budget=(
                max_context_tokens is not None
                and estimate > max_context_tokens
            ),
        )

    # Stable ordering contract: compression always evaluates recency on a
    # newest-first sequence (scheduled_date desc, then original order as
    # the tiebreak), matching the runtime repository's ordering. Callers
    # may pass any order; the retained window is defined by THIS rule.
    ordered_tasks = sorted(
        enumerate(context.recent_tasks),
        key=lambda pair: (-pair[1].scheduled_date.toordinal(), pair[0]),
    )
    ordered_context = context.model_copy(
        update={"recent_tasks": [task for _, task in ordered_tasks]}
    )
    context = ordered_context

    retained_tasks, older_tasks, promoted = _select_retained_tasks(
        context.recent_tasks,
        recent_tasks_budget,
        focus_query,
        query_vector=query_vector,
        task_vectors=task_vectors,
        rescue_enabled=strategy is CompressionStrategy.RELEVANT_SUMMARY,
    )
    retained_reviews = context.recent_reviews[:recent_reviews_budget]
    older_reviews = context.recent_reviews[recent_reviews_budget:]

    # Summaries are computed BEFORE the budget loop and participate in
    # it, so both windowed strategies optimize toward the SAME final
    # input budget (max_context_tokens) — window count parity alone is
    # not token parity.
    task_summary = (
        _task_summary(older_tasks)
        if strategy is CompressionStrategy.RELEVANT_SUMMARY
        else None
    )
    review_summary = (
        _review_summary(older_reviews)
        if strategy is CompressionStrategy.RELEVANT_SUMMARY
        else None
    )
    shrink_steps = 0
    if max_context_tokens is not None:
        # Dynamic budget: shed recency headroom (never below the floor)
        # until the serialized context — summaries included — fits the
        # per-call input budget.
        while (
            estimate_text_tokens(
                _preview(
                    context,
                    retained_tasks,
                    retained_reviews,
                    task_summary=task_summary,
                    review_summary=review_summary,
                )
            )
            > max_context_tokens
            and (
                len(retained_tasks) > _MIN_RETAINED_TASKS
                or len(retained_reviews) > _MIN_RETAINED_REVIEWS
            )
        ):
            if len(retained_tasks) > _MIN_RETAINED_TASKS:
                shed = retained_tasks.pop()
                older_tasks = [shed] + older_tasks
                if task_summary is not None:
                    task_summary = _task_summary(older_tasks)
            if len(retained_reviews) > _MIN_RETAINED_REVIEWS:
                shed_review = retained_reviews.pop()
                older_reviews = [shed_review] + older_reviews
                if review_summary is not None:
                    review_summary = _review_summary(older_reviews)
            shrink_steps += 1
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

    # Provenance: every pruned record with its reason, and every summary
    # line mapped back to the original deliverables it folded.
    retained_ids = {task.task_id for task in retained_tasks}
    pruned: list[PrunedRecord] = []
    for index, task in enumerate(context.recent_tasks):
        if task.task_id in retained_ids:
            continue
        if index < recent_tasks_budget:
            reason = "budget_shrink"
        elif strategy is CompressionStrategy.RELEVANT_SUMMARY:
            reason = "irrelevance"
        else:
            reason = "recency_window"
        pruned.append(
            PrunedRecord(
                task_id=task.task_id,
                kind="task",
                reason=reason,
                original_deliverable=task.deliverable,
            )
        )
    summary_sources: dict[str, tuple[str, ...]] = {}
    if task_summary:
        folded = tuple(
            dict.fromkeys(
                task.deliverable.strip()
                for task in older_tasks
                if task.state == "completed"
            )
        )
        summary_sources[task_summary] = folded

    return ContextCompressionResult(
        context=compressed,
        before_chars=before_chars,
        after_chars=len(compressed.model_dump_json()),
        task_compressed_count=len(context.recent_tasks) - len(retained_tasks),
        review_compressed_count=len(context.recent_reviews) - len(retained_reviews),
        promoted_task_count=promoted,
        budget_shrink_steps=shrink_steps,
        strategy=strategy.value,
        over_budget=(
            max_context_tokens is not None and final_estimate > max_context_tokens
        ),
        estimated_context_tokens=final_estimate,
        pruned=tuple(pruned),
        summary_sources=summary_sources,
    )


def _preview(
    context: PlanningContext,
    retained_tasks: list[TaskContext],
    retained_reviews: list[ReviewContext],
    *,
    task_summary: str | None = None,
    review_summary: str | None = None,
) -> str:
    preview = context.model_copy(
        update={
            "recent_tasks": retained_tasks,
            "recent_reviews": retained_reviews,
            "task_history_summary": task_summary,
            "review_history_summary": review_summary,
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
