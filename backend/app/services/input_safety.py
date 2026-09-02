"""Shared deterministic safety preflight used before any external model call."""

import re

from app.schemas.agent_runs import RiskResult

HIGH_RISK_PATTERNS = (
    ("risk_self_harm_zh", re.compile(r"自杀|结束生命|伤害自己")),
    ("risk_self_harm_en", re.compile(r"\b(suicide|kill myself|self[- ]harm)\b", re.I)),
)


def assess_input_risk(message: str) -> RiskResult:
    matched = [rule_id for rule_id, pattern in HIGH_RISK_PATTERNS if pattern.search(message)]
    return RiskResult(
        level="high" if matched else "none",
        category="self_harm" if matched else None,
        method="rule",
        matched_rule_ids=matched,
        confidence=1 if matched else None,
    )
