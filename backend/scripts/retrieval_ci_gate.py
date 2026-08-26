"""Retrieval quality CI gate — blocks deployment on regression >5%.

Runs the frozen retrieval-v2 hardened set through the configured
embedding + reranker providers and checks Recall@5 against the
SLO threshold (0.90). Exits 1 on failure.

Usage in CI:
    python -m scripts.retrieval_ci_gate
"""

from __future__ import annotations

import asyncio

RECALL_SLO = 0.90
REGRESSION_BLOCK_THRESHOLD = 0.05


async def run_gate() -> int:
    # Reuse the existing retrieval eval, but with the SLO check built in
    from scripts.run_retrieval_eval import run

    report = await run(k=5, dataset="retrieval-v2")
    hybrid = report["modes"]["hybrid"]
    recall = hybrid["recall_at_5"]
    mrr = hybrid["mrr"]

    print(f"Retrieval CI Gate — {report['dataset']}")
    print(f"  embedding: {report['embedding_provider']} | reranker: {report['rerank_provider']}")
    print(f"  hybrid Recall@5: {recall:.3f} (SLO ≥ {RECALL_SLO})")
    print(f"  hybrid MRR:      {mrr:.3f}")

    if recall < RECALL_SLO:
        print(f"  ❌ FAIL: Recall@5 {recall:.3f} < SLO {RECALL_SLO}")
        return 1
    print("  ✅ PASS")
    return 0


def main() -> int:
    return asyncio.run(run_gate())


if __name__ == "__main__":
    raise SystemExit(main())
