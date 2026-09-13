"""Real-embedding synonym separation measurement (修复8 quantification).

20 synonym-rewrite pairs (career domain) + 10 unrelated controls are
scored two ways: lexical char-bigram overlap and embedding-3 cosine via
the configured real provider. Acceptance: cosine separates synonyms from
unrelated controls at high accuracy; lexical scoring fails on synonyms
(rewrites share no bigrams) — proving the hybrid relevance upgrade is
a real capability gain, not a stub-tested assumption.
"""

from __future__ import annotations

import asyncio
import json

from app.core.config import get_settings
from app.providers.embedding_api import OpenAICompatibleEmbeddingProvider

SYNONYM_PAIRS = [
    ("跳槽", "换工作"),
    ("简历", "履历"),
    ("面试", "面谈"),
    ("复盘", "回顾总结"),
    ("实习", "见习"),
    ("offer", "录取通知"),
    ("算法题", "编程题"),
    ("大厂", "头部公司"),
    ("转行", "换赛道"),
    ("秋招", "秋季校园招聘"),
    ("刷题", "做编程练习"),
    ("八股文", "基础知识问答"),
    ("内推", "内部推荐"),
    ("deadline", "截止日期"),
    ("STAR法则", "情境任务行动结果描述法"),
    ("投递", "提交申请"),
    ("背调", "背景调查"),
    ("试用期", "考察期"),
    ("年终奖", "年度奖金"),
    ("远程办公", "居家办公"),
]

UNRELATED_PAIRS = [
    ("跳槽", "做饭"),
    ("简历", "健身"),
    ("面试", "旅游"),
    ("复盘", "购物"),
    ("实习", "游戏"),
    ("算法题", "看电影"),
    ("大厂", "睡觉"),
    ("转行", "养猫"),
    ("刷题", "听音乐"),
    ("内推", "开车"),
]


def _bigrams(text: str) -> set[str]:
    normalized = "".join(text.split()).lower()
    return (
        {normalized[i : i + 2] for i in range(len(normalized) - 1)}
        if len(normalized) >= 2
        else set()
    )


def _lexical(a: str, b: str) -> float:
    ga, gb = _bigrams(a), _bigrams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / min(len(ga), len(gb))


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


async def main() -> int:
    settings = get_settings()
    provider = OpenAICompatibleEmbeddingProvider(
        base_url=str(settings.embedding_base_url),
        api_key=settings.embedding_api_key.get_secret_value(),
        model_name=settings.embedding_api_model or "embedding-3",
        timeout_seconds=20,
    )
    phrases = sorted({p for pair in SYNONYM_PAIRS + UNRELATED_PAIRS for p in pair})
    vectors = await provider.embed(phrases)
    vec_by_phrase = dict(zip(phrases, vectors, strict=True))

    def _score(pairs, metric):
        values = []
        for a, b in pairs:
            if metric == "lexical":
                values.append(_lexical(a, b))
            else:
                values.append(_cosine(vec_by_phrase[a], vec_by_phrase[b]))
        return values

    syn_lex = _score(SYNONYM_PAIRS, "lexical")
    syn_cos = _score(SYNONYM_PAIRS, "cosine")
    un_lex = _score(UNRELATED_PAIRS, "lexical")
    un_cos = _score(UNRELATED_PAIRS, "cosine")

    def _accuracy(pos, neg):
        best = 0.0
        for threshold in [i / 100 for i in range(101)]:
            correct = sum(1 for v in pos if v >= threshold) + sum(
                1 for v in neg if v < threshold
            )
            best = max(best, correct / (len(pos) + len(neg)))
        return best

    def _mean(values):
        return round(sum(values) / len(values), 4) if values else None

    report = {
        "model": settings.embedding_api_model or "embedding-3",
        "synonym_pairs": len(SYNONYM_PAIRS),
        "unrelated_controls": len(UNRELATED_PAIRS),
        "lexical": {
            "synonym_mean": _mean(syn_lex),
            "unrelated_mean": _mean(un_lex),
            "separation_accuracy": round(_accuracy(syn_lex, un_lex), 3),
        },
        "embedding_cosine": {
            "synonym_mean": _mean(syn_cos),
            "unrelated_mean": _mean(un_cos),
            "separation_accuracy": round(_accuracy(syn_cos, un_cos), 3),
        },
        "per_pair": [
            {"a": a, "b": b, "lexical": round(lx, 3), "cosine": round(cs, 3)}
            for (a, b), lx, cs in zip(SYNONYM_PAIRS, syn_lex, syn_cos, strict=True)
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    await provider.aclose() if hasattr(provider, "aclose") else None
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
