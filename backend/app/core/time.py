"""Product-local calendar helpers.

Persisted timestamps stay in UTC. Calendar dates shown to users follow the
product timezone so every use case agrees around midnight.
"""

from datetime import UTC, date, datetime, timedelta, timezone

PRODUCT_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")


def product_today(now: datetime | None = None) -> date:
    """Return the product-local date for an aware or UTC-naive timestamp."""

    value = now or datetime.now(UTC)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(PRODUCT_TIMEZONE).date()
