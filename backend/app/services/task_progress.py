"""Pure helpers for turning a Task starter action into executable checklist steps."""

import re
from datetime import date, timedelta


def parse_execution_steps(value: str) -> list[str]:
    """Parse the compact ordered text emitted by the planner into stable UI steps."""

    normalized = re.sub(r"[；;]\s*(?=\d+[.、]\s*)", "\n", value.strip())
    parts = re.split(r"\n+|\s+(?=\d+[.、]\s*)", normalized)
    steps = [re.sub(r"^\d+[.、]\s*", "", item.strip()) for item in parts]
    result = [item for item in steps if item]
    return result or [value.strip()]


def fixed_cycle_contains(*, plan_date: date, horizon_end: date, target: date) -> bool:
    """Return whether target belongs to the visible fixed cycle for a Plan."""

    return plan_date <= target <= min(plan_date + timedelta(days=6), horizon_end)
