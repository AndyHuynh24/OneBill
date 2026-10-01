from datetime import UTC, datetime

import pytest

from api.me.logic import get_or_create_profile

NOW = datetime(2026, 9, 29, 12, 0, 0, tzinfo=UTC)
LATER = datetime(2026, 10, 1, 8, 30, 0, tzinfo=UTC)


def test_first_call_creates_profile_with_defaults(table):
    profile = get_or_create_profile(table, "abc-123", now=NOW)

    assert profile == {
        "userId": "abc-123",
        "baseCurrency": "USD",
        "createdAt": "2026-09-29T12:00:00+00:00",
    }
    stored = table.get_item(Key={"PK": "USER#abc-123", "SK": "PROFILE"})["Item"]
    assert stored["type"] == "PROFILE"
    assert stored["userId"] == "abc-123"


def test_second_call_returns_existing_profile_unchanged(table):
    first = get_or_create_profile(table, "abc-123", now=NOW)
    second = get_or_create_profile(table, "abc-123", now=LATER)

    assert second == first
    assert second["createdAt"] == "2026-09-29T12:00:00+00:00"


def test_profiles_are_isolated_per_user(table):
    get_or_create_profile(table, "user-a", now=NOW)
    get_or_create_profile(table, "user-b", now=LATER)

    a = table.get_item(Key={"PK": "USER#user-a", "SK": "PROFILE"})["Item"]
    b = table.get_item(Key={"PK": "USER#user-b", "SK": "PROFILE"})["Item"]
    assert a["createdAt"] != b["createdAt"]


class _RacingTable:
    """Simulates a concurrent first call: another request creates the profile
    after our get_item misses but before our put_item runs."""

    def __init__(self, table, winner_item):
        self._table = table
        self._winner_item = winner_item
        self._raced = False

    def get_item(self, **kwargs):
        if not self._raced:
            self._raced = True
            self._table.put_item(Item=self._winner_item)
            return {}
        return self._table.get_item(**kwargs)

    def put_item(self, **kwargs):
        return self._table.put_item(**kwargs)


def test_concurrent_first_call_returns_the_winning_item(table):
    winner = {
        "PK": "USER#abc-123",
        "SK": "PROFILE",
        "type": "PROFILE",
        "userId": "abc-123",
        "baseCurrency": "USD",
        "createdAt": "2026-09-29T11:59:59+00:00",
    }

    profile = get_or_create_profile(_RacingTable(table, winner), "abc-123", now=NOW)

    assert profile["createdAt"] == "2026-09-29T11:59:59+00:00"


@pytest.mark.parametrize("bad_id", ["", "   ", None])
def test_rejects_empty_user_id(table, bad_id):
    with pytest.raises(ValueError, match="user_id"):
        get_or_create_profile(table, bad_id, now=NOW)
