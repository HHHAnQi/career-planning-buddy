"""Component-based required-fact retention scoring (frozen v2, 2026-08-31).

Pre-registered BEFORE the v2 offline run (docs/standards/metric-registry.md).
Scoring objects are ONLY the text windows the MODEL ACTUALLY RECEIVES
(retained records and summary lines) — never hidden source lists or
annotation answers. A fact is judged against its BEST single window so
that components (object, numbers, status) must survive TOGETHER.

Verdicts: "retained" | "lost" | "needs_review" (needs_review is NEVER
counted as retained).

Component rules:
  numbers   every digit token of the fact must appear verbatim in the
            window (30 分钟 vs 90 分钟 → lost);
  status    status/negation tokens (已完成/未完成/未/不/放弃) present in
            the fact must appear in the window, and a flipped antonym in
            the window → lost (已完成 → 未完成);
  anchors   ≥2 distinctive anchors (ASCII entities + non-generic CJK
            bigrams, request-echo excluded) must match in ONE window;
  coverage  ≥60% of the fact's non-generic bigrams inside that window —
            truncations keeping only keywords fall below and are lost;
  between   anchors pass but coverage sits in [30%, 60%) with no number
            or status discrepancy → needs_review (human follow-up).
"""

from __future__ import annotations

import re

from evals.v2.graders.model import _distinctive_anchors, _text_bigrams

_GENERIC_BIGRAMS = _FUNCTIONAL_PHRASES = (
    "计划 任务 完成 进行 需要 可以 一个 相关 提高 提升 分析 整理 准备 "
    "制定 记录 帮助 情况 内容 通过 检查 优化 撰写 梳理 每天 学习 复习"
).split()

_STATUS_TOKENS = ("已完成", "未完成", "已放弃", "放弃")
_ANTONYM = {"已完成": "未完成", "未完成": "已完成"}
# General negation pairs: any fact term X with a "未X"/"没X" variant in
# the clause counts as a status flip (e.g. 收到 -> 未收到).
_NEGATION_PREFIXES = ("未", "没")


def _split_clauses(text: str) -> list[str]:
    """Split a window into clauses so status/negation is judged LOCALLY —
    a summary line containing both 'A 已完成' and 'B 未完成' must not
    let B's negation leak into A's verdict."""
    import re

    return [c for c in re.split(r"[；;。\n，,、]", text) if c.strip()]


def _number_set(text: str) -> set[str]:
    """EXACT digit-run set. Substring matching is a bug: '30' must NOT
    match inside '130', and '2' must not match inside '20'."""
    return set(re.findall(r"\d+(?:\.\d+)?", text))


def _negation_flip(fact: str, clause: str) -> bool:
    for term in re.findall(r"[\u4e00-\u9fff]{2,6}", fact):
        for prefix in _NEGATION_PREFIXES:
            if (prefix + term) in clause and term not in _STATUS_TOKENS:
                return True
    return False


def _fact_bigrams(fact: str) -> set[str]:
    grams = _text_bigrams(fact)
    return {
        g for g in grams if not any(g in phrase for phrase in _GENERIC_BIGRAMS)
    }


def _numbers(text: str) -> list[str]:
    return sorted(_number_set(text))


def score_fact_in_clauses(
    fact: str, clauses: list[str], *, request_text: str = ""
) -> tuple[str, dict[str, object]]:
    """Clause-level variant: windows are pre-split clauses of the ACTUAL
    rendered input (see scripts/context_compression_eval.py v3)."""
    return score_fact(fact, clauses, request_text=request_text)


def score_fact(
    fact: str, windows: list[str], *, request_text: str = ""
) -> tuple[str, dict[str, object]]:
    """Score one required fact against model-visible windows (clauses).

    Numbers compare as EXACT digit-run sets (no substring matches);
    status tokens and negation flips are judged within the SINGLE best
    clause, so mixed-status summaries cannot leak state across objects.
    """

    fact_numbers = _number_set(fact)
    fact_status = [t for t in _STATUS_TOKENS if t in fact]
    fact_grams = _fact_bigrams(fact)

    clauses: list[str] = []
    for window in windows:
        clauses.extend(_split_clauses(window))

    best: dict[str, object] | None = None
    best_key: tuple[int, float] | None = None
    for clause in clauses:
        anchors = _distinctive_anchors(fact, clause, request_text=request_text)
        covered = len(fact_grams & _text_bigrams(clause))
        coverage = covered / len(fact_grams) if fact_grams else 1.0
        clause_numbers = _number_set(clause)
        numbers_ok = fact_numbers <= clause_numbers
        clause_status = [t for t in _STATUS_TOKENS if t in clause]
        status_flip = any(
            _ANTONYM.get(tok) in clause_status for tok in fact_status
        ) or _negation_flip(fact, clause)
        status_ok = all(tok in clause for tok in fact_status) and not status_flip
        candidate = {
            "clause": clause[:80],
            "anchors": anchors,
            "coverage": round(coverage, 3),
            "numbers_ok": numbers_ok,
            "status_ok": status_ok,
        }
        key = (anchors, coverage)
        if best_key is None or key > best_key:
            best, best_key = candidate, key

    if best is None or not clauses:
        return "lost", {"reason": "no_model_input_clauses"}

    if not best["numbers_ok"]:
        return "lost", {**best, "reason": "number_mismatch"}
    if not best["status_ok"]:
        return "lost", {**best, "reason": "status_mismatch_or_flip"}
    if best["anchors"] < 2:  # type: ignore[operator]
        return "lost", {**best, "reason": "insufficient_anchors"}
    if best["coverage"] >= 0.6:  # type: ignore[operator]
        return "retained", best
    if best["coverage"] >= 0.3:  # type: ignore[operator]
        return "needs_review", {**best, "reason": "partial_coverage"}
    return "lost", {**best, "reason": "truncated_keywords_only"}
