"""Tests for estimated per-token cost accounting."""

from __future__ import annotations

from decimal import Decimal

from app.providers.pricing import estimate_cost_cny, is_estimated


def test_glm_pricing_uses_45_official_anchor() -> None:
    # 1M input + 1M output at GLM-4.5 list price: 0.8 + 2.0 = 2.8 CNY.
    cost = estimate_cost_cny("glm-4.7", 1_000_000, 1_000_000)
    assert cost == Decimal("2.800000")
    assert is_estimated("glm-4.7")


def test_small_call_and_prefix_matching() -> None:
    # 1000 in + 500 out at (0.0008, 0.002)/1K: 0.8 + 1.0 fen-level.
    assert estimate_cost_cny("glm-4.6", 1_000, 500) == Decimal("0.001800")


def test_unknown_model_costs_zero() -> None:
    assert estimate_cost_cny("mystery-model", 9_999, 9_999) == Decimal("0")
    assert estimate_cost_cny(None, 100, 100) == Decimal("0")
    assert not is_estimated("mystery-model")
