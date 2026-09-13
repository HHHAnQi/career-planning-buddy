"""Lightweight query normalization for retrieval.

Industry practice (Glean/Perplexity): the retrieval pipeline's first
step is query understanding. This module provides a deterministic,
zero-cost normalization layer — synonym expansion for common career
terms, filler removal, and whitespace normalization. No LLM call; safe
for the eval harness's determinism contract.

Heavier techniques (LLM query expansion, HyDE) are recorded as future
work — they add latency and cost and should be measured against the
CI gate before enabling.
"""

from __future__ import annotations

import re

# Career-domain synonyms: each group maps to a canonical form that is
# appended to the query (not replacing — preserving the original signal).
_SYNONYM_GROUPS: list[tuple[str, list[str]]] = [
    ("简历", ["CV", "curriculum vitae", "履历"]),
    ("面试", ["interview", "面经"]),
    ("实习", ["internship", "intern"]),
    ("秋招", ["秋季招聘", "校招"]),
    ("春招", ["春季招聘"]),
    ("后端", ["backend", "服务端"]),
    ("前端", ["frontend", "客户端"]),
    ("算法", ["algorithm", "ML", "机器学习"]),
    ("大模型", ["LLM", "大语言模型", "GPT"]),
    ("Agent", ["智能体", "代理"]),
    ("RAG", ["检索增强", "检索增强生成"]),
    ("微服务", ["microservice"]),
    ("分布式", ["distributed"]),
    ("性能优化", ["调优", "performance"]),
]

_FILLER_PATTERNS = [
    re.compile(r"(帮我|请|麻烦|麻烦您|能否|可以|想要|想|需要)", re.I),
    re.compile(r"(看一下|查一下|搜一下|找一下|看看|查查|搜搜)"),
    re.compile(r"(谢谢|感谢|多谢)"),
    re.compile(r"\s{2,}"),
]


def normalize_query(query: str) -> str:
    """Return the query with domain synonyms expanded and fillers removed.

    The canonical synonym is APPENDED (not replacing) so both the
    original phrasing and the canonical term are available to the
    vector and lexical channels.
    """
    cleaned = query.strip()
    for pattern in _FILLER_PATTERNS:
        cleaned = pattern.sub(" ", cleaned)
    cleaned = " ".join(cleaned.split()).strip()
    if not cleaned:
        cleaned = query.strip()  # never return empty

    expansions: list[str] = []
    lowered = cleaned.lower()
    for canonical, variants in _SYNONYM_GROUPS:
        if canonical.lower() in lowered:
            continue  # canonical already present
        if any(v.lower() in lowered for v in variants):
            expansions.append(canonical)

    if expansions:
        return f"{cleaned} {' '.join(expansions)}"
    return cleaned
