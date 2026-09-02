"""Counterexample tests for the v2 fact-retention scoring (frozen rules).

Each case pins one failure mode the v1 scorer missed:
  1. fact present ONLY in the (unsent) source list → must be LOST;
  2. 30 minutes rewritten to 90 → number mismatch → LOST;
  3. 已完成 flipped to 未完成 → status flip → LOST;
  4. truncation keeping keywords but dropping the rest → LOST;
  5. borderline partial coverage → NEEDS_REVIEW, never counted retained.
"""

from __future__ import annotations

from evals.context_metrics import score_fact


def test_fact_only_in_source_list_is_lost() -> None:
    # The model received only the summary line; the original deliverable
    # lives in the (unsent) provenance list and must NOT count.
    model_windows = ["更早任务已完成：每日固定复盘"]
    verdict, detail = score_fact(
        "针对 JD 定制简历：完成 3 个版本", model_windows
    )
    assert verdict == "lost", detail


def test_number_rewrite_30_to_90_is_lost() -> None:
    model_windows = ["每日 90 分钟算法练习安排"]
    verdict, detail = score_fact("每日 30 分钟算法练习", model_windows)
    assert verdict == "lost", detail
    assert detail["reason"] == "number_mismatch"


def test_status_flip_completed_to_incomplete_is_lost() -> None:
    model_windows = ["OS 篇笔记未完成，遗留 2 节"]
    verdict, detail = score_fact("已完成八股文笔记 OS 篇", model_windows)
    assert verdict == "lost", detail


def test_truncation_with_surviving_keywords_is_lost() -> None:
    model_windows = ["图论专题练习安排"]  # keywords survive, substance gone
    verdict, detail = score_fact(
        "刷完 LeetCode 图论专题 20 题并写题解", model_windows
    )
    assert verdict == "lost", detail


def test_partial_coverage_is_needs_review_not_retained() -> None:
    model_windows = ["投递多家公司，转化率待观察的渠道调整"]
    verdict, detail = score_fact(
        "投递 12 家公司后转向内推渠道并记录转化率", model_windows
    )
    assert verdict in {"needs_review", "lost"}, detail
    assert verdict != "retained"


def test_genuine_survival_is_retained() -> None:
    model_windows = ["历史记录：投递 12 家公司后转向内推渠道，转化率提升"]
    verdict, detail = score_fact(
        "投递 12 家公司后转向内推渠道", model_windows
    )
    assert verdict == "retained", detail


def test_request_echo_does_not_count() -> None:
    # The window only echoes the user request wording, not the fact.
    model_windows = ["结合最近复盘调整下周安排"]
    verdict, _ = score_fact(
        "每故事补量化结果",
        model_windows,
        request_text="结合最近复盘调整下周安排",
    )
    assert verdict == "lost"


def test_number_substring_130_vs_30_is_lost() -> None:
    # v2 bug: "30" matched inside "130" via substring containment.
    model_windows = ["每日 130 分钟高强度算法训练"]
    verdict, detail = score_fact("每日 30 分钟算法训练", model_windows)
    assert verdict == "lost", detail
    assert detail["reason"] == "number_mismatch"


def test_number_substring_20_vs_2_is_lost() -> None:
    model_windows = ["完成 SQL 手写题 20 道打卡"]
    verdict, detail = score_fact("练习 SQL 手写题 2 道", model_windows)
    assert verdict == "lost", detail


def test_negation_received_vs_not_received_is_lost() -> None:
    model_windows = ["投递批次结束，尚未收到任何面试邀约"]
    verdict, detail = score_fact("收到 2 个面试邀约", model_windows)
    assert verdict == "lost", detail


def test_mixed_status_summary_does_not_leak_across_objects() -> None:
    # One summary line, two objects with OPPOSITE states: A completed,
    # B incomplete. Fact about A must not inherit B's negation.
    model_windows = [
        "复盘：项目 A 收尾已完成；项目 B 联调未完成，遗留接口 3 个"
    ]
    verdict_a, detail_a = score_fact("项目 A 收尾已完成", model_windows)
    assert verdict_a == "retained", detail_a
    verdict_b, _ = score_fact("项目 B 联调已完成", model_windows)
    assert verdict_b == "lost"
