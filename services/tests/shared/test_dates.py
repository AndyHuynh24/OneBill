from datetime import UTC, date, datetime

import pytest

from shared.dates import app_today, parse_iso_date


def test_app_today_uses_pacific_time():
    # 03:00 UTC on Oct 1 is still the evening of Sep 30 in Los Angeles.
    assert app_today(datetime(2026, 10, 1, 3, 0, tzinfo=UTC)) == date(2026, 9, 30)


def test_app_today_after_pacific_midnight():
    assert app_today(datetime(2026, 10, 1, 8, 0, tzinfo=UTC)) == date(2026, 10, 1)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-09-30", date(2026, 9, 30)),
        ("2026-9-30", None),
        ("2026-02-30", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_iso_date(raw, expected):
    assert parse_iso_date(raw) == expected
