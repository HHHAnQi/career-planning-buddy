"""Estimated per-token cost accounting for real LLM providers.

Prices are CNY per 1,000 tokens, keyed by the model_id reported by the
provider. Figures marked ``proxy`` use the closest officially published
price as an estimation anchor — e.g. Zhipu publishes verbatim split
pricing only for GLM-4.5 (input ¥0.8 / output ¥2 per million tokens);
newer GLM versions render prices frontend-only, so the 4.5 anchor is used
until a verbatim number is published. All costs computed through this
module are ESTIMATES (list price × usage), not billing data.

Unknown models cost 0 — the same honest default as before, but now the
known ones are accounted.
"""

from __future__ import annotations

from decimal import Decimal

# CNY per 1K tokens: model prefix -> (input, output)
_PRICES: dict[str, tuple[Decimal, Decimal]] = {
    # Zhipu official GLM-4.5 pricing (¥0.8/M in, ¥2/M out), verified
    # 2026-08; used as proxy for glm-4.6/4.7 until verbatim prices are
    # published.
    "glm-4.5": (Decimal("0.0008"), Decimal("0.002")),
    "glm-4.6": (Decimal("0.0008"), Decimal("0.002")),
    "glm-4.7": (Decimal("0.0008"), Decimal("0.002")),
    # DeepSeek public pricing reference (2026-08): chat tier.
    "deepseek-chat": (Decimal("0.002"), Decimal("0.008")),
    "deepseek-v4-flash": (Decimal("0.002"), Decimal("0.008")),
    "deepseek-v4-pro": (Decimal("0.004"), Decimal("0.016")),
}

# Suffix quality tiers we do not price separately (reasoning variants bill
# under the base model id in practice for these families).


def estimate_cost_cny(model_id: str | None, tokens_in: int, tokens_out: int) -> Decimal:
    """Estimated cost in CNY for one call; 0 for unknown models."""

    if not model_id:
        return Decimal("0")
    normalized = model_id.lower()
    for prefix, (price_in, price_out) in _PRICES.items():
        if normalized.startswith(prefix):
            return (
                Decimal(tokens_in) / Decimal(1000) * price_in
                + Decimal(tokens_out) / Decimal(1000) * price_out
            ).quantize(Decimal("0.000001"))
    return Decimal("0")


def is_estimated(model_id: str | None) -> bool:
    """Whether this model has a price anchor (vs. unpriced → 0)."""

    if not model_id:
        return False
    normalized = model_id.lower()
    return any(normalized.startswith(prefix) for prefix in _PRICES)
