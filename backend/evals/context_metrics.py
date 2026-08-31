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

_STATUS_TOKENS = ("已完成", "未完成", "已放弃", "放弃", "未")
_ANTONYM = {"已完成": "未完成", "未完成": "已完成"}


def _fact_bigrams(fact: str) -> set[str]:
    grams = _text_bigrams(fact)
    return {
        g for g in grams if not any(g in phrase for phrase in _GENERIC_BIGRAMS)
    }


def _numbers(text: str) -> list[str]:
    return re.findall(r"\d+(?:\.\d+)?", text)


def score_fact(
    fact: str, windows: list[str], *, request_text: str = ""
) -> tuple[str, dict[str, object]]:
    """Score one required fact against the model-visible windows."""

    fact_numbers = _numbers(fact)
    fact_status = [t for t in _STATUS_TOKENS if t in fact]
    fact_grams = _fact_bigrams(fact)

    best: dict[str, object] | None = None
    for window in windows:
        anchors = _distinctive_anchors(fact, window, request_text=request_text)
        covered = len(fact_grams & _text_bigrams(window))
        coverage = covered / len(fact_grams) if fact_grams else 1.0
        numbers_ok = all(n in window for n in fact_numbers)
        window_status = [t for t in _STATUS_TOKENS if t in window]
        status_flip = any(
            _ANTONYM.get(tok) in window_status for tok in fact_status
        )
        status_ok = (
            all(any(tok in w for w in [window]) for tok in fact_status)
            and not status_flip
        )
        candidate = {
            "anchors": anchors,
            "coverage": round(coverage, 3),
            "numbers_ok": numbers_ok,
            "status_ok": status_ok,
        }
        if best is None or (anchors, coverage) > (best["anchors"], best["coverage"]):  # type: ignore[index]
            best = candidate

    if best is None or not windows:
        return "lost", {"reason": "no_model_input_windows"}

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
