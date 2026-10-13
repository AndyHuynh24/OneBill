"""The Gmail script's ingest key: shown once, stored only as a hash, one per user, revocable."""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from subscriptions.ingest import (
    DAILY_EMAIL_LIMIT,
    GLOBAL_MONTHLY_EMAIL_LIMIT,
    NotAllowed,
    consume_email_quota,
    create_key,
    key_status,
    record_use,
    revoke_key,
    verify_key,
)

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
UPLOADERS = {"owners"}


def everything(table):
    return table.scan()["Items"]


def test_a_new_key_works_and_only_its_hash_is_stored(table):
    key = create_key(table, "user-a", UPLOADERS, NOW)

    assert key.startswith("obk_")
    assert verify_key(table, f"Bearer {key}", NOW) == "user-a"
    stored = str(everything(table))
    assert key not in stored  # never the key itself
    assert hashlib.sha256(key.encode()).hexdigest() in stored


def test_only_the_owner_can_create_a_key(table):
    """Decision S2: model calls stay invite-only, like scanning."""
    with pytest.raises(NotAllowed):
        create_key(table, "user-b", set(), NOW)


def test_a_new_key_replaces_the_old_one(table):
    old = create_key(table, "user-a", UPLOADERS, NOW)

    new = create_key(table, "user-a", UPLOADERS, NOW + timedelta(minutes=5))

    assert verify_key(table, f"Bearer {old}", NOW) is None
    assert verify_key(table, f"Bearer {new}", NOW) == "user-a"
    assert sum(1 for i in everything(table) if i["PK"].startswith("INGESTKEY#")) == 1


def test_revoking_disconnects(table):
    key = create_key(table, "user-a", UPLOADERS, NOW)

    revoke_key(table, "user-a")

    assert verify_key(table, f"Bearer {key}", NOW) is None
    assert key_status(table, "user-a") == {
        "connected": False,
        "createdAt": None,
        "expiresOn": None,
        "lastUsedAt": None,
    }
    revoke_key(table, "user-a")  # already revoked: fine


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "obk_abc",
        "Bearer",
        "Bearer obk_" + "a" * 43 + "x",
        "Basic dXNlcjpwYXNz",
        "Bearer ../../USER#a",
    ],
)
def test_malformed_or_unknown_keys_are_rejected(table, header):
    create_key(table, "user-a", UPLOADERS, NOW)

    assert verify_key(table, header, NOW) is None


def test_status_shows_when_the_script_last_sent_something(table):
    create_key(table, "user-a", UPLOADERS, NOW)
    record_use(table, "user-a", NOW + timedelta(hours=1))

    status = key_status(table, "user-a")

    assert status["connected"] is True
    assert status["createdAt"] == NOW.isoformat()
    assert status["lastUsedAt"] == (NOW + timedelta(hours=1)).isoformat()


def test_daily_email_cap_bounds_cost_if_a_script_loops(table):
    for _ in range(DAILY_EMAIL_LIMIT):
        assert consume_email_quota(table, "user-a", NOW)

    assert not consume_email_quota(table, "user-a", NOW)
    assert consume_email_quota(table, "user-b", NOW)  # per user (until the all-users cap)
    assert consume_email_quota(table, "user-a", NOW + timedelta(days=1))  # resets daily


def test_an_all_users_monthly_cap_bounds_cost_even_with_several_leaked_keys(table):
    assert GLOBAL_MONTHLY_EMAIL_LIMIT == 300  # 2026-10-12, Andy (was 30 a day)
    cap = 30  # a small cap keeps the test quick; same code path
    users = [f"user-{i}" for i in range(cap // DAILY_EMAIL_LIMIT + 1)]
    sent = sum(
        consume_email_quota(table, u, NOW, global_limit=cap)
        for u in users
        for _ in range(DAILY_EMAIL_LIMIT)
    )

    assert sent == cap
    usage = {i["PK"]: i for i in everything(table) if i["SK"].startswith("USAGE#")}
    assert int(usage[f"USER#{users[-1]}"]["emails"]) == 0  # refused emails aren't charged to them
    # The next day doesn't help: the cap is for the month.
    assert not consume_email_quota(table, "user-new", NOW + timedelta(days=1), global_limit=cap)


def test_a_key_stops_working_after_a_year(table):
    key = create_key(table, "user-a", UPLOADERS, NOW)

    assert verify_key(table, f"Bearer {key}", NOW + timedelta(days=364)) == "user-a"
    assert verify_key(table, f"Bearer {key}", NOW + timedelta(days=366)) is None
    assert key_status(table, "user-a")["expiresOn"] == (NOW + timedelta(days=365)).isoformat()


def test_a_key_made_before_expiry_existed_lasts_a_year_from_its_creation(table):
    key = create_key(table, "user-a", UPLOADERS, NOW)
    for item in everything(table):
        if "expiresOn" in item:
            table.update_item(
                Key={"PK": item["PK"], "SK": item["SK"]}, UpdateExpression="REMOVE expiresOn"
            )

    assert verify_key(table, f"Bearer {key}", NOW + timedelta(days=366)) is None


def test_the_email_counter_shares_the_daily_usage_item_with_uploads(table):
    consume_email_quota(table, "user-a", NOW)

    usage = {(i["PK"], i["SK"]): i for i in everything(table) if i["SK"].startswith("USAGE#")}
    today, month = f"USAGE#{NOW.date().isoformat()}", f"USAGE#{NOW.strftime('%Y-%m')}"
    assert int(usage[("USER#user-a", today)]["emails"]) == 1
    assert int(usage[("GLOBAL", month)]["emails"]) == 1  # the all-users cap counts it
    assert int(usage[("GLOBAL", today)]["emails"]) == 1  # and today's count, to watch
    assert int(usage[("USER#user-a", today)]["expiresAt"]) > NOW.timestamp()
