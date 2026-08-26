"""Unit tests for standard retrieval metrics (Recall@K, MRR, nDCG@K)."""

from __future__ import annotations

import pytest

from evals.retrieval_metrics import (
    gate_accuracy,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    summarize,
)


def test_perfect_ranking_scores_one() -> None:
    ranked = ["a", "b", "c"]
    relevant = {"a", "b"}
    assert recall_at_k(ranked, relevant, 5) == 1.0
    assert mrr(ranked, relevant) == 1.0
    assert ndcg_at_k(ranked, relevant, 5) == pytest.approx(1.0)


def test_partial_recall_at_k() -> None:
    ranked = ["x", "a", "y", "b"]
    relevant = {"a", "b"}
    assert recall_at_k(ranked, relevant, 2) == 0.5
    assert recall_at_k(ranked, relevant, 4) == 1.0
    assert recall_at_k(ranked, relevant, 1) == 0.0
    assert recall_at_k(ranked, relevant, 0) == 0.0


def test_mrr_reciprocal_of_first_hit() -> None:
    assert mrr(["x", "y", "a"], {"a"}) == pytest.approx(1 / 3)
    assert mrr(["a"], {"a"}) == 1.0
    assert mrr(["x", "y"], {"a"}) == 0.0


def test_ndcg_penalizes_late_hits() -> None:
    early = ndcg_at_k(["a", "x"], {"a"}, 2)
    late = ndcg_at_k(["x", "a"], {"a"}, 2)
    assert early > late > 0.0


def test_empty_relevant_rejected_and_summarize_averages() -> None:
    with pytest.raises(ValueError):
        recall_at_k(["a"], set(), 5)
    with pytest.raises(ValueError):
        summarize([])
    summary = summarize(
        [(["a", "b"], {"a"}), (["b", "a"], {"a"})], k=2
    )
    assert summary["recall_at_2"] == 1.0
    assert summary["mrr"] == pytest.approx(0.75)
    # (1.0 + 1/log2(3)) / 2 — the second case hits at rank 2.
    assert summary["ndcg_at_2"] == pytest.approx((1.0 + 1.0 / __import__('math').log2(3)) / 2)


def test_precision_at_k() -> None:
    # 1 golden in 5 results: P@5 = 1/5 = 0.2
    assert precision_at_k(["a", "x", "y", "z", "w"], {"a"}, 5) == pytest.approx(0.2)
    # 2 goldens in top-3: P@3 = 2/3
    assert precision_at_k(["a", "b", "c"], {"a", "b"}, 3) == pytest.approx(2 / 3)
    # No golden in top-K: P = 0
    assert precision_at_k(["x", "y", "z"], {"a"}, 3) == 0.0
    # Fewer results than K: denominator is actual result count
    assert precision_at_k(["a", "x"], {"a"}, 5) == pytest.approx(0.5)


def test_gate_accuracy() -> None:
    # Golden retrievable + sufficient=True → correct
    assert gate_accuracy(["a", "b"], {"a"}, sufficient=True) is True
    # Golden retrievable + sufficient=False → FALSE REJECTION (bug!)
    assert gate_accuracy(["a", "b"], {"a"}, sufficient=False) is False
    # Nothing retrievable + sufficient=False → correct rejection
    assert gate_accuracy([], {"a"}, sufficient=False) is True
    # Nothing retrievable + sufficient=True → false acceptance
    assert gate_accuracy([], {"a"}, sufficient=True) is False


def test_summarize_includes_precision() -> None:
    summary = summarize([(["a", "b"], {"a"}), (["b", "a"], {"a"})], k=2)
    assert "precision_at_2" in summary
    assert summary["precision_at_2"] == pytest.approx(0.5)
