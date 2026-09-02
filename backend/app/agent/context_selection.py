"""Deterministic Stage 6A memory retrieval, ranking, and budgeting."""

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from math import exp, log
from uuid import UUID

from app.models.evidence import Memory
from app.providers.embedding import EmbeddingProvider
from app.repositories.evidence import EvidenceRepository


@dataclass(frozen=True, slots=True)
class ScoredMemory:
    memory_id: UUID
    version: int
    memory_type: str
    summary: str
    pinned: bool
    similarity: float
    recency: float
    final_score: float


@dataclass(frozen=True, slots=True)
class MemorySelectionResult:
    selected: list[ScoredMemory]
    query_hash: str
    pinned_count: int
    semantic_count: int
    fallback_used: bool
    retrieval_failed: bool


def build_memory_query(
    *,
    user_message: str,
    goal_type: str,
    blockers: list[str],
    adjustment_request: str | None,
) -> str:
    parts = [user_message.strip(), goal_type.strip()]
    parts.extend(blocker.strip() for blocker in blockers if blocker.strip())
    if adjustment_request and adjustment_request.strip():
        parts.append(adjustment_request.strip())
    return "\n".join(dict.fromkeys(part for part in parts if part))[:2000]


def recency_score(
    *,
    last_used_at: datetime | None,
    updated_at: datetime,
    now: datetime,
    half_life_days: int = 14,
) -> float:
    reference = max(value for value in (last_used_at, updated_at) if value is not None)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    age_days = max((now - reference).total_seconds() / 86400, 0.0)
    return max(0.0, min(1.0, exp(-log(2) * age_days / half_life_days)))


def combine_memory_score(*, similarity: float, recency: float) -> float:
    return max(0.0, min(1.0, 0.8 * similarity + 0.2 * recency))


def select_memories_within_budget(
    candidates: list[ScoredMemory],
    *,
    max_items: int,
    max_chars: int,
) -> list[ScoredMemory]:
    ordered = sorted(
        candidates,
        key=lambda item: (
            not item.pinned,
            -item.final_score,
            str(item.memory_id),
        ),
    )
    selected: list[ScoredMemory] = []
    used_chars = 0
    seen: set[UUID] = set()
    for candidate in ordered:
        if candidate.memory_id in seen or len(selected) >= max_items:
            continue
        item_chars = len(candidate.summary)
        if used_chars + item_chars > max_chars:
            continue
        selected.append(candidate)
        seen.add(candidate.memory_id)
        used_chars += item_chars
    return selected


def _memory_category(memory: Memory) -> str:
    """Return a Memory's counterfactual ``content_json.category`` string.

    Pre-PR-8 Memories carry no ``category`` key; this helper returns an
    empty string for them so the default ``exclude_categories`` (also
    empty) sees no false positives. PR-8 planted Memories use one of
    ``relevant`` / ``irrelevant`` / ``conflicting`` / ``visible`` /
    ``hidden``.
    """

    raw = memory.content_json.get("category") if memory.content_json else None
    return str(raw) if isinstance(raw, str) else ""


async def select_memories(
    *,
    repository: EvidenceRepository,
    embedding_provider: EmbeddingProvider,
    user_id: UUID,
    user_message: str,
    goal_type: str,
    blockers: list[str],
    adjustment_request: str | None,
    semantic_enabled: bool,
    retrieval_limit: int,
    max_items: int,
    max_chars: int,
    min_similarity: float,
    half_life_days: int,
    now: datetime | None = None,
    # PR-8: counterfactual memory ablation. When non-empty, candidate
    # Memory rows carrying any of these categories (in content_json/category)
    # are excluded from the planning catalog (pinned + retrieved). The
    # memory_lookup Tool path does NOT use this filter, so planted
    # "deferred" memories stay reachable through explicit lookups.
    exclude_categories: set[str] | None = None,
    # Pre-embedded query vector (parallel fan-out path embeds outside the
    # DB transaction so the network call overlaps the evidence branch).
    precomputed_vector: list[float] | None = None,
) -> MemorySelectionResult:
    selected_at = now or datetime.now(UTC)
    query = build_memory_query(
        user_message=user_message,
        goal_type=goal_type,
        blockers=blockers,
        adjustment_request=adjustment_request,
    )
    query_hash = sha256(query.encode("utf-8")).hexdigest()
    excluded = exclude_categories or set()
    try:
        pinned_rows = await repository.pinned_memories(user_id, limit=max_items)
    except Exception:  # Retrieval is explicitly best-effort and must not fail the Run.
        return MemorySelectionResult([], query_hash, 0, 0, False, True)

    candidates: list[ScoredMemory] = []
    pinned_ids: set[UUID] = set()
    for memory in pinned_rows:
        if _memory_category(memory) in excluded:
            continue
        pinned_ids.add(memory.id)
        recency = recency_score(
            last_used_at=memory.last_used_at,
            updated_at=memory.updated_at,
            now=selected_at,
            half_life_days=half_life_days,
        )
        candidates.append(
            ScoredMemory(
                memory_id=memory.id,
                version=memory.version,
                memory_type=memory.memory_type,
                summary=memory.summary,
                pinned=True,
                similarity=1.0,
                recency=recency,
                final_score=1.0,
            )
        )

    fallback_used = False
    retrieval_failed = False
    if semantic_enabled and query:
        vector: list[float] | None = precomputed_vector
        if vector is None:
            try:
                vectors = await embedding_provider.embed([query])
                vector = vectors[0] if vectors else None
            except Exception:
                fallback_used = True
        try:
            semantic_rows = await repository.memory_lookup(
                user_id=user_id,
                query=query,
                vector=vector,
                limit=retrieval_limit,
            )
            for memory, similarity in semantic_rows:
                if memory.id in pinned_ids or similarity < min_similarity:
                    continue
                if _memory_category(memory) in excluded:
                    continue
                recency = recency_score(
                    last_used_at=memory.last_used_at,
                    updated_at=memory.updated_at,
                    now=selected_at,
                    half_life_days=half_life_days,
                )
                candidates.append(
                    ScoredMemory(
                        memory_id=memory.id,
                        version=memory.version,
                        memory_type=memory.memory_type,
                        summary=memory.summary,
                        pinned=False,
                        similarity=similarity,
                        recency=recency,
                        final_score=combine_memory_score(
                            similarity=similarity,
                            recency=recency,
                        ),
                    )
                )
        except Exception:
            retrieval_failed = True

    selected = select_memories_within_budget(
        candidates,
        max_items=max_items,
        max_chars=max_chars,
    )
    if selected:
        try:
            await repository.touch_memories(
                user_id=user_id,
                memory_ids=[item.memory_id for item in selected],
                used_at=selected_at,
            )
        except Exception:
            retrieval_failed = True
    return MemorySelectionResult(
        selected=selected,
        query_hash=query_hash,
        pinned_count=sum(item.pinned for item in selected),
        semantic_count=sum(not item.pinned for item in selected),
        fallback_used=fallback_used,
        retrieval_failed=retrieval_failed,
    )
