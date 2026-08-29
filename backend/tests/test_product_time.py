"""Product-local calendar boundary tests."""

from datetime import UTC, date, datetime

from app.core.time import product_today


def test_product_today_uses_asia_shanghai_calendar_boundaries() -> None:
    assert product_today(datetime(2026, 8, 12, 16, 30, tzinfo=UTC)) == date(2026, 8, 13)
    assert product_today(datetime(2026, 8, 12, 23, 59, tzinfo=UTC)) == date(2026, 8, 13)
    assert product_today(datetime(2026, 8, 13, 0, 0, tzinfo=UTC)) == date(2026, 8, 13)
    assert product_today(datetime(2026, 8, 13, 15, 59, tzinfo=UTC)) == date(2026, 8, 13)
