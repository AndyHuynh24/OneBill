"""Saving subscriptions: one SUB# per service, the renewal reminder kept in step, emails deduped."""

from datetime import UTC, date, datetime

from boto3.dynamodb.conditions import Key

from subscriptions.parse import ParsedSubscription
from subscriptions.store import claim_email, finish_email, save_subscription

USER = "user-a"
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def netflix(**overrides):
    fields = {
        "service": "Netflix",
        "service_key": "netflix",
        "plan": "Standard",
        "amount_cents": 1549,
        "currency": "USD",
        "cycle": "monthly",
        "status": "charged",
        "charge_date": date(2026, 10, 1),
        "period_end": date(2026, 10, 31),
        "next_renewal": date(2026, 11, 1),
    }
    return ParsedSubscription(**(fields | overrides))


def sub_item(table, key="netflix"):
    return table.get_item(Key={"PK": f"USER#{USER}", "SK": f"SUB#{key}"}).get("Item")


def obligations(table):
    return table.query(
        KeyConditionExpression=Key("PK").eq(f"USER#{USER}") & Key("SK").begins_with("OBL#")
    )["Items"]


def due_on(table, day):
    return table.query(IndexName="GSI1", KeyConditionExpression=Key("GSI1PK").eq(f"DUE#{day}"))[
        "Items"
    ]


def test_a_new_subscription_and_its_renewal_reminder(table):
    save_subscription(table, USER, netflix(), date(2026, 10, 1), NOW)

    item = sub_item(table)
    assert (item["service"], item["amountCents"], item["cycle"], item["status"]) == (
        "Netflix",
        1549,
        "monthly",
        "charged",
    )
    assert item["nextRenewal"] == "2026-11-01"
    assert [h["amountCents"] for h in item["history"]] == [1549]
    (reminder,) = due_on(table, "2026-11-01")  # the daily reminders job will find it
    assert (reminder["kind"], reminder["vendor"], reminder["amountCents"]) == (
        "renewal",
        "Netflix",
        1549,
    )
    assert reminder["subKey"] == "netflix"
    assert item["obligationKey"] == reminder["SK"]


def test_next_months_email_moves_the_reminder(table):
    save_subscription(table, USER, netflix(), date(2026, 10, 1), NOW)

    save_subscription(
        table,
        USER,
        netflix(
            charge_date=date(2026, 11, 1),
            period_end=date(2026, 11, 30),
            next_renewal=date(2026, 12, 1),
        ),
        date(2026, 11, 1),
        NOW,
    )

    assert due_on(table, "2026-11-01") == []
    assert len(due_on(table, "2026-12-01")) == 1
    assert len(obligations(table)) == 1
    assert sub_item(table)["nextRenewal"] == "2026-12-01"


def test_the_same_renewal_again_keeps_the_reminder_and_what_was_already_sent(table):
    save_subscription(table, USER, netflix(), date(2026, 10, 1), NOW)
    (reminder,) = obligations(table)
    table.update_item(
        Key={"PK": reminder["PK"], "SK": reminder["SK"]},
        UpdateExpression="SET remindersSent = :s",
        ExpressionAttributeValues={":s": [7]},
    )

    save_subscription(table, USER, netflix(status="upcoming"), date(2026, 10, 25), NOW)

    (same,) = obligations(table)
    assert same["SK"] == reminder["SK"]
    assert same["remindersSent"] == [7]


def test_a_price_change_is_kept_in_history(table):
    save_subscription(table, USER, netflix(), date(2026, 10, 1), NOW)

    save_subscription(
        table,
        USER,
        netflix(amount_cents=1799, charge_date=date(2026, 11, 1), next_renewal=date(2026, 12, 1)),
        date(2026, 11, 1),
        NOW,
    )

    item = sub_item(table)
    assert item["amountCents"] == 1799
    assert item["previousAmountCents"] == 1549
    assert item["priceChangedAt"] == "2026-11-01"
    assert [h["amountCents"] for h in item["history"]] == [1549, 1799]


def test_cancelling_stops_the_reminders(table):
    save_subscription(table, USER, netflix(), date(2026, 10, 1), NOW)

    save_subscription(
        table, USER, netflix(status="cancelled", next_renewal=None), date(2026, 10, 20), NOW
    )

    item = sub_item(table)
    assert item["status"] == "cancelled"
    assert item["nextRenewal"] is None
    assert obligations(table) == []


def test_a_trial_gets_a_trial_ends_reminder(table):
    trial = netflix(status="trial_ending", amount_cents=1549, next_renewal=date(2026, 10, 15))

    save_subscription(table, USER, trial, date(2026, 10, 8), NOW)

    (reminder,) = obligations(table)
    assert reminder["kind"] == "trial_ends"


def test_an_older_email_only_adds_history(table):
    save_subscription(
        table,
        USER,
        netflix(charge_date=date(2026, 11, 1), next_renewal=date(2026, 12, 1)),
        date(2026, 11, 1),
        NOW,
    )

    save_subscription(
        table, USER, netflix(amount_cents=1499, charge_date=date(2026, 9, 1)), date(2026, 9, 1), NOW
    )

    item = sub_item(table)
    assert item["nextRenewal"] == "2026-12-01"  # the newer email still decides
    assert item["amountCents"] == 1549
    assert [h["date"] for h in item["history"]] == ["2026-09-01", "2026-11-01"]


def test_an_email_without_an_amount_keeps_the_known_price(table):
    save_subscription(table, USER, netflix(), date(2026, 10, 1), NOW)

    save_subscription(
        table, USER, netflix(amount_cents=None, status="upcoming"), date(2026, 10, 25), NOW
    )

    assert sub_item(table)["amountCents"] == 1549


def test_history_keeps_the_last_twelve_charges_without_duplicates(table):
    for month in range(1, 13):
        charged = date(2026, month, 1)
        save_subscription(
            table,
            USER,
            netflix(charge_date=charged, next_renewal=None, period_end=None),
            charged,
            NOW,
        )
    save_subscription(
        table,
        USER,
        netflix(charge_date=date(2026, 12, 1), next_renewal=None, period_end=None),
        date(2026, 12, 1),
        NOW,
    )
    save_subscription(
        table,
        USER,
        netflix(charge_date=date(2027, 1, 1), next_renewal=None, period_end=None),
        date(2027, 1, 1),
        NOW,
    )

    history = sub_item(table)["history"]
    assert len(history) == 12
    assert history[0]["date"] == "2026-02-01"
    assert history[-1]["date"] == "2027-01-01"


def test_each_email_is_processed_once_unless_it_failed(table):
    assert claim_email(table, USER, "18c2f0a9d3b4e5f6", NOW)
    assert not claim_email(table, USER, "18c2f0a9d3b4e5f6", NOW)  # duplicate send

    finish_email(table, USER, "18c2f0a9d3b4e5f6", "failed", 0)
    assert claim_email(table, USER, "18c2f0a9d3b4e5f6", NOW)  # a failed one may be retried

    finish_email(table, USER, "18c2f0a9d3b4e5f6", "processed", 1)
    assert not claim_email(table, USER, "18c2f0a9d3b4e5f6", NOW)


def test_email_markers_hold_no_content_and_expire(table):
    claim_email(table, USER, "<CAF=abc@mail.gmail.com>", NOW)

    (marker,) = table.query(
        KeyConditionExpression=Key("PK").eq(f"USER#{USER}") & Key("SK").begins_with("EMAIL#")
    )["Items"]
    assert "CAF" not in marker["SK"]  # message IDs are hashed into the key
    assert set(marker) <= {
        "PK",
        "SK",
        "type",
        "status",
        "subscriptions",
        "createdAt",
        "updatedAt",
        "expiresAt",
    }
    assert int(marker["expiresAt"]) > NOW.timestamp() + 80 * 86400


def test_an_email_stuck_processing_can_be_retried_after_a_few_minutes(table):
    """A request cut off mid-way (API Gateway's 30 s limit) leaves the marker 'processing'."""
    from datetime import timedelta

    assert claim_email(table, USER, "abc", NOW)

    assert not claim_email(table, USER, "abc", NOW + timedelta(minutes=1))  # still in flight
    assert claim_email(table, USER, "abc", NOW + timedelta(minutes=6))  # abandoned: retry


# --- the app: list, edit, cancel ---------------------------------------------------------------

import pytest  # noqa: E402

from subscriptions.store import (  # noqa: E402
    InvalidEdit,
    SubscriptionNotFound,
    edit_subscription,
    list_subscriptions,
)


def test_list_puts_active_subscriptions_first_by_next_renewal(table):
    save_subscription(table, USER, netflix(), date(2026, 10, 1), NOW)
    spotify = netflix(
        service="Spotify", service_key="spotify", amount_cents=1199, next_renewal=date(2026, 10, 20)
    )
    save_subscription(table, USER, spotify, date(2026, 10, 1), NOW)
    gym = netflix(service="Gym", service_key="gym", status="cancelled", next_renewal=None)
    save_subscription(table, USER, gym, date(2026, 10, 1), NOW)

    subs = list_subscriptions(table, USER)

    assert [s["serviceKey"] for s in subs] == ["spotify", "netflix", "gym"]
    assert subs[0]["amountCents"] == 1199  # plain ints, not Decimals
    assert "PK" not in subs[0]
    assert list_subscriptions(table, "someone-else") == []


def test_cancel_from_the_app_stops_reminders(table):
    save_subscription(table, USER, netflix(), date(2026, 10, 1), NOW)

    sub = edit_subscription(table, USER, "netflix", {"action": "cancel"}, NOW)

    assert (sub["status"], sub["nextRenewal"]) == ("cancelled", None)
    assert obligations(table) == []


def test_editing_the_renewal_date_moves_the_reminder(table):
    save_subscription(table, USER, netflix(), date(2026, 10, 1), NOW)

    sub = edit_subscription(
        table,
        USER,
        "netflix",
        {"action": "edit", "fields": {"nextRenewal": "2026-11-05", "amountCents": 1699}},
        NOW,
    )

    assert (sub["nextRenewal"], sub["amountCents"]) == ("2026-11-05", 1699)
    assert sub["renewalComputed"] is False
    (reminder,) = obligations(table)
    assert reminder["dueDate"] == "2026-11-05"


def test_unknown_or_someone_elses_subscription_is_not_found(table):
    save_subscription(table, "user-b", netflix(), date(2026, 10, 1), NOW)

    with pytest.raises(SubscriptionNotFound):
        edit_subscription(table, USER, "netflix", {"action": "cancel"}, NOW)


@pytest.mark.parametrize(
    "body",
    [
        {"action": "delete"},
        {"action": "edit", "fields": {}},
        {"action": "edit", "fields": {"amountCents": -5}},
        {"action": "edit", "fields": {"amountCents": 1.5}},
        {"action": "edit", "fields": {"nextRenewal": "soon"}},
        {"action": "edit", "fields": {"cycle": "fortnightly"}},
        {"action": "edit", "fields": {"status": "charged"}},
    ],
)
def test_invalid_edits_are_rejected(table, body):
    save_subscription(table, USER, netflix(), date(2026, 10, 1), NOW)

    with pytest.raises(InvalidEdit):
        edit_subscription(table, USER, "netflix", body, NOW)


def test_malformed_keys_are_not_found(table):
    with pytest.raises(SubscriptionNotFound):
        edit_subscription(table, USER, "../PROFILE", {"action": "cancel"}, NOW)


# --- deleting, and turning reminders off ------------------------------------------------------

from subscriptions.store import delete_subscription, set_reminders  # noqa: E402


def test_delete_removes_the_subscription_its_reminder_and_its_email_markers(table):
    """So re-sending the same emails (e.g. to test again) reads them again."""
    claim_email(table, USER, "netflix-oct", NOW)
    save_subscription(table, USER, netflix(), date(2026, 10, 1), NOW)
    finish_email(table, USER, "netflix-oct", "processed", 1, ["netflix"])
    claim_email(table, USER, "spotify-oct", NOW)
    finish_email(table, USER, "spotify-oct", "processed", 1, ["spotify"])

    delete_subscription(table, USER, "netflix")

    assert sub_item(table) is None
    assert obligations(table) == []
    assert claim_email(table, USER, "netflix-oct", NOW)  # can be read again
    assert not claim_email(table, USER, "spotify-oct", NOW)  # other services untouched


def test_deleting_an_unknown_subscription_is_not_found(table):
    with pytest.raises(SubscriptionNotFound):
        delete_subscription(table, USER, "netflix")
    with pytest.raises(SubscriptionNotFound):
        delete_subscription(table, USER, "../PROFILE")


def test_a_deleted_subscription_can_come_back_with_new_emails(table):
    save_subscription(table, USER, netflix(), date(2026, 10, 1), NOW)
    delete_subscription(table, USER, "netflix")

    save_subscription(table, USER, netflix(), date(2026, 11, 1), NOW)

    assert sub_item(table)["service"] == "Netflix"


def test_reminders_off_removes_the_reminder_and_keeps_future_renewals_quiet(table):
    save_subscription(table, USER, netflix(), date(2026, 10, 1), NOW)

    sub = set_reminders(table, USER, "netflix", on=False, now=NOW)
    save_subscription(table, USER, netflix(next_renewal=date(2026, 12, 1)), date(2026, 11, 1), NOW)

    assert sub["remindersOff"] is True
    assert obligations(table) == []
    assert sub_item(table)["nextRenewal"] == "2026-12-01"  # still tracked and in the total


def test_reminders_back_on_restores_the_next_one(table):
    save_subscription(table, USER, netflix(), date(2026, 10, 1), NOW)
    set_reminders(table, USER, "netflix", on=False, now=NOW)

    set_reminders(table, USER, "netflix", on=True, now=NOW)

    (reminder,) = obligations(table)
    assert reminder["dueDate"] == "2026-11-01"


# --- added by hand (rent, utilities, anything without emails) ----------------------------------

from subscriptions.store import AlreadyTracked, add_subscription, advance_past_renewal  # noqa: E402


def test_add_a_bill_by_hand_with_a_payment_due_reminder(table):
    rent = {
        "service": "Rent",
        "category": "bill",
        "amountCents": 210000,
        "cycle": "monthly",
        "nextRenewal": "2026-11-01",
    }

    sub = add_subscription(table, USER, rent, NOW)

    assert (sub["service"], sub["category"], sub["source"]) == ("Rent", "bill", "manual")
    assert (sub["amountCents"], sub["nextRenewal"]) == (210000, "2026-11-01")
    (reminder,) = obligations(table)
    assert (reminder["kind"], reminder["vendor"], reminder["dueDate"]) == (
        "payment_due",
        "Rent",
        "2026-11-01",
    )


def test_add_a_subscription_by_hand_says_renews(table):
    gym = {"service": "Gym", "amountCents": 4000, "cycle": "monthly", "nextRenewal": "2026-10-15"}

    add_subscription(table, USER, gym, NOW)

    assert obligations(table)[0]["kind"] == "renewal"
    assert sub_item(table, "gym")["category"] == "subscription"


def test_an_electric_bill_can_be_added_without_knowing_the_amount(table):
    add_subscription(
        table, USER, {"service": "PG&E", "category": "bill", "nextRenewal": "2026-10-20"}, NOW
    )

    assert sub_item(table, "pg e")["amountCents"] is None


def test_adding_something_already_tracked_is_refused(table):
    save_subscription(table, USER, netflix(), date(2026, 10, 1), NOW)

    with pytest.raises(AlreadyTracked):
        add_subscription(table, USER, {"service": "netflix", "nextRenewal": "2026-11-01"}, NOW)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"service": "", "nextRenewal": "2026-11-01"},
        {"service": "x" * 81, "nextRenewal": "2026-11-01"},
        {"service": "Rent"},  # needs a date
        {"service": "Rent", "nextRenewal": "soon"},
        {"service": "Rent", "nextRenewal": "2026-11-01", "amountCents": -1},
        {"service": "Rent", "nextRenewal": "2026-11-01", "cycle": "fortnightly"},
        {"service": "Rent", "nextRenewal": "2026-11-01", "category": "loan"},
        {"service": "???", "nextRenewal": "2026-11-01"},  # nothing to key on
    ],
)
def test_invalid_manual_entries_are_rejected(table, body):
    with pytest.raises(InvalidEdit):
        add_subscription(table, USER, body, NOW)


def test_after_the_due_date_passes_it_rolls_to_the_next_one(table):
    """Nobody emails 'rent is due again': the daily job moves the date and the reminder."""
    add_subscription(
        table,
        USER,
        {"service": "Rent", "category": "bill", "amountCents": 210000, "nextRenewal": "2026-10-01"},
        NOW,
    )

    moved = advance_past_renewal(table, USER, "rent", today=date(2026, 10, 2), now=NOW)

    assert moved
    item = sub_item(table, "rent")
    assert item["nextRenewal"] == "2026-11-01"
    (reminder,) = obligations(table)
    assert reminder["dueDate"] == "2026-11-01"


def test_rolling_forward_keeps_the_day_of_month(table):
    add_subscription(table, USER, {"service": "Rent", "nextRenewal": "2026-01-31"}, NOW)

    advance_past_renewal(table, USER, "rent", today=date(2026, 4, 2), now=NOW)

    assert sub_item(table, "rent")["nextRenewal"] == "2026-04-30"  # Jan 31 → … → Apr 30


def test_nothing_rolls_if_it_isnt_past_cancelled_or_has_no_cycle(table):
    add_subscription(table, USER, {"service": "Rent", "nextRenewal": "2026-11-01"}, NOW)
    add_subscription(
        table, USER, {"service": "Gym", "nextRenewal": "2026-09-01", "cycle": "other"}, NOW
    )

    assert not advance_past_renewal(table, USER, "rent", today=date(2026, 10, 2), now=NOW)
    assert not advance_past_renewal(table, USER, "gym", today=date(2026, 10, 2), now=NOW)
    assert not advance_past_renewal(table, USER, "missing", today=date(2026, 10, 2), now=NOW)
