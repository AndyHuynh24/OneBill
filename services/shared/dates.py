"""Calendar dates for deadlines and reminders.

Obligations are calendar dates (a return window closes on a *day*), so "today" must be computed
in a time zone. For now there's one app-wide zone; per-user zones can come later.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

APP_TIMEZONE = ZoneInfo("America/Los_Angeles")


def app_today(now: datetime) -> date:
    return now.astimezone(APP_TIMEZONE).date()


def parse_iso_date(value: str | None) -> date | None:
    """Strict YYYY-MM-DD, or None."""
    if not value or len(value) != 10:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
