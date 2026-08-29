"""Standard retrieval metrics: Recall@K, Precision@K, MRR, nDCG@K, Gate Accuracy.

Pure functions over (ranked result ids, relevant id set) so they are
reusable by tests, the retrieval eval runner, and CI gates.

Conventions:
* ``ranked`` is the system's output order (position 0 = rank 1).
* ``relevant`` is the golden set of chunk ids (or any hashable ids)
  judged relevant for the query.
* All metrics are in [0, 1]; empty ``relevant`` raises ValueError — a
  metric without a golden answer is meaningless by definition.
"""

from __future__ import annotations

from collections.abc import Hashable, Sequence


def _validate(ranked: Sequence[Hashable], relevant: set[Hashable]) -> None:
    if not relevant:
        raise ValueError("relevant set must not be empty")


def recall_at_k(
    ranked: Sequence[Hashable], relevant: set[Hashable], k: int
) -> float:
    """|relevant ∩ top-k| / |relevant| — did we FIND the golden?"""

    _validate(ranked, relevant)
    if k <= 0:
        return 0.0
    top_k = set(ranked[:k])
    return len(top_k & relevant) / len(relevant)


def precision_at_k(
    ranked: Sequence[Hashable], relevant: set[Hashable], k: int
) -> float:
    """|relevant ∩ top-k| / k — how CLEAN is the top-K we send to the LLM?

    High precision = fewer wasted context tokens and lower hallucination
    risk. With 1 golden in a K=5 result set, max precision is 1/5 = 0.2;
    the complement (1 - P@K) measures the noise fraction.
    """

    _validate(ranked, relevant)
    if k <= 0:
        return 0.0
    top_k = set(ranked[:k])
    return len(top_k & relevant) / min(k, len(ranked) or 1)


def gate_accuracy(
    ranked: Sequence[Hashable],
    relevant: set[Hashable],
    *,
    sufficient: bool,
) -> bool:
    """Whether the sufficiency decision was CORRECT for this case.

    A gate that says sufficient=False when the golden IS retrievable is
    a false rejection (user sees 'no information' when an answer exists).
    A gate that says sufficient=True when NO golden exists is a false
    acceptance (garbage context sent to the LLM). This metric catches both.
    """

    _validate(ranked, relevant)
    golden_retrievable = bool(ranked)
    if golden_retrievable:
        return sufficient  # should have been True
    return not sufficient  # should have been False


def mrr(ranked: Sequence[Hashable], relevant: set[Hashable]) -> float:
    """1 / rank of the first relevant result; 0 when none is relevant."""

    _validate(ranked, relevant)
    for rank, item in enumerate(ranked, start=1):
        if item in relevant:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(
    ranked: Sequence[Hashable], relevant: set[Hashable], k: int
) -> float:
    """Normalized discounted cumulative gain with binary relevance.

    DCG  = Σ_{i=1..k} rel(i) / log2(i + 1)
    IDCG = DCG of the ideal ordering (all relevant items first).
    """

    _validate(ranked, relevant)
    if k <= 0:
        return 0.0

    import math

    def dcg(order: Sequence[Hashable]) -> float:
        return sum(
            (1.0 if item in relevant else 0.0) / math.log2(index + 2)
            for index, item in enumerate(order[:k])
        )

    ideal_length = min(len(relevant), k)
    ideal_dcg = sum(1.0 / math.log2(index + 2) for index in range(ideal_length))
    if ideal_dcg == 0.0:
        return 0.0
    return dcg(ranked) / ideal_dcg


def summarize(
    cases: Sequence[tuple[Sequence[Hashable], set[Hashable]]],
    *,
    k: int = 5,
) -> dict[str, float]:
    """Average Recall/Precision/MRR/nDCG@k over (ranked, relevant) cases."""

    if not cases:
        raise ValueError("cases must not be empty")
    return {
        f"recall_at_{k}": sum(
            recall_at_k(ranked, relevant, k) for ranked, relevant in cases
        )
        / len(cases),
        f"precision_at_{k}": sum(
            precision_at_k(ranked, relevant, k) for ranked, relevant in cases
        )
        / len(cases),
        "mrr": sum(mrr(ranked, relevant) for ranked, relevant in cases)
        / len(cases),
        f"ndcg_at_{k}": sum(
            ndcg_at_k(ranked, relevant, k) for ranked, relevant in cases
        )
        / len(cases),
    }
