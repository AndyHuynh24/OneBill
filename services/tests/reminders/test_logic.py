from datetime import date

import pytest

from api.me.logic import set_email_reminders
from reminders.logic import (
    SEND_NOW_DAILY_CAP,
    collect_due,
    mark_sent,
    render_digest,
    run_reminders,
    send_now,
    unmark_sent,
)

TODAY = date(2026, 9, 30)


def put_obligation(
    table, user, due, obl_id, kind="payment_due", vendor="Target", amount=2618, sent=()
):
    item = {
        "PK": f"USER#{user}",
        "SK": f"OBL#{due}#{obl_id}",
        "type": "OBLIGATION",
        "oblId": obl_id,
        "docId": "1790000000000aaaaaaaa",
        "kind": kind,
        "dueDate": due,
        "vendor": vendor,
        "amountCents": amount,
        "remindersSent": list(sent),
        "GSI1PK": f"DUE#{due}",
        "GSI1SK": f"USER#{user}#OBL#{obl_id}",
    }
    table.put_item(Item=item)
    return item


def get(table, user, due, obl_id):
    return table.get_item(Key={"PK": f"USER#{user}", "SK": f"OBL#{due}#{obl_id}"})["Item"]


# --- collecting -----------------------------------------------------------------------------------


def test_collects_obligations_due_in_7_3_and_1_days(table):
    put_obligation(table, "a", "2026-10-07", "o7")  # +7
    put_obligation(table, "a", "2026-10-03", "o3")  # +3
    put_obligation(table, "b", "2026-10-01", "o1")  # +1
    put_obligation(table, "a", "2026-10-05", "o5")  # +5: not a reminder day
    put_obligation(table, "a", "2026-09-30", "o0")  # today: not a reminder day

    due = collect_due(table, TODAY)

    assert {(user, offset, item["oblId"]) for user, offset, item in due} == {
        ("a", 7, "o7"),
        ("a", 3, "o3"),
        ("b", 1, "o1"),
    }


def test_skips_offsets_already_sent(table):
    put_obligation(table, "a", "2026-10-03", "o3", sent=[3])

    assert collect_due(table, TODAY) == []


# --- marking (idempotency) -------------------------------------------------------------------


def test_mark_sent_only_once(table):
    item = put_obligation(table, "a", "2026-10-03", "o3")

    assert mark_sent(table, item, 3) is True
    assert mark_sent(table, item, 3) is False  # a retry can't claim it again
    assert get(table, "a", "2026-10-03", "o3")["remindersSent"] == [3]


def test_unmark_sent_removes_only_that_offset(table):
    item = put_obligation(table, "a", "2026-10-03", "o3", sent=[7])
    mark_sent(table, item, 3)

    unmark_sent(table, item, 3)

    assert get(table, "a", "2026-10-03", "o3")["remindersSent"] == [7]


# --- the digest -------------------------------------------------------------------------------


def test_digest_lists_items_soonest_first():
    items = [
        (
            7,
            {
                "kind": "warranty_expires",
                "vendor": "Best Buy",
                "dueDate": "2026-10-07",
                "amountCents": 89999,
            },
        ),
        (
            1,
            {"kind": "payment_due", "vendor": "PG&E", "dueDate": "2026-10-01", "amountCents": 8410},
        ),
    ]

    subject, text, html = render_digest(items, TODAY, app_url="http://localhost:5173")

    assert subject == "OneBill: 2 things due soon"
    assert text.index("PG&E") < text.index("Best Buy")
    assert "Payment due" in text
    assert "Thu, Oct 1 (tomorrow)" in text
    assert "$84.10" in text
    assert "Wed, Oct 7 (in 7 days)" in text
    assert "http://localhost:5173" in text
    assert "PG&amp;E" in html  # escaped in HTML


def test_digest_subject_singular():
    subject, _, _ = render_digest(
        [
            (
                3,
                {
                    "kind": "payment_due",
                    "vendor": "Target",
                    "dueDate": "2026-10-03",
                    "amountCents": 2618,
                },
            )
        ],
        TODAY,
        app_url="http://localhost:5173",
    )

    assert subject == "OneBill: 1 thing due soon"


def test_digest_escapes_untrusted_vendor_names_in_html():
    _, _, html = render_digest(
        [(3, {"kind": "payment_due", "vendor": "<script>x</script>", "dueDate": "2026-10-03"})],
        TODAY,
        app_url="http://localhost:5173",
    )

    assert "<script>" not in html


# --- the whole run ----------------------------------------------------------------------------


class FakeMailer:
    def __init__(self, fail_for=()):
        self.sent = []
        self.fail_for = set(fail_for)

    def __call__(self, to, subject, text, html):
        if to in self.fail_for:
            raise RuntimeError("SES unavailable")
        self.sent.append((to, subject))


DIRECTORY = {"a": "a@example.com", "b": "b@example.com"}


def test_run_sends_one_digest_per_user(table):
    put_obligation(table, "a", "2026-10-07", "o7")
    put_obligation(table, "a", "2026-10-03", "o3")
    put_obligation(table, "b", "2026-10-01", "o1")
    mailer = FakeMailer()

    summary = run_reminders(table, TODAY, DIRECTORY.get, mailer, app_url="http://localhost:5173")

    assert sorted(to for to, _ in mailer.sent) == ["a@example.com", "b@example.com"]
    assert summary == {"users": 2, "emails": 2, "items": 3, "skipped": 0, "failed": 0}
    assert get(table, "a", "2026-10-07", "o7")["remindersSent"] == [7]


def test_rerun_on_the_same_day_sends_nothing(table):
    put_obligation(table, "a", "2026-10-03", "o3")
    run_reminders(table, TODAY, DIRECTORY.get, FakeMailer(), app_url="x")
    mailer = FakeMailer()

    run_reminders(table, TODAY, DIRECTORY.get, mailer, app_url="x")

    assert mailer.sent == []


def test_user_without_a_verified_email_is_skipped(table):
    put_obligation(table, "nobody", "2026-10-03", "o3")
    mailer = FakeMailer()

    summary = run_reminders(table, TODAY, DIRECTORY.get, mailer, app_url="x")

    assert mailer.sent == []
    assert summary["skipped"] == 1
    assert get(table, "nobody", "2026-10-03", "o3")["remindersSent"] == []


def test_send_failure_unmarks_so_the_next_run_retries(table):
    put_obligation(table, "a", "2026-10-03", "o3")
    put_obligation(table, "b", "2026-10-01", "o1")
    mailer = FakeMailer(fail_for={"a@example.com"})

    summary = run_reminders(table, TODAY, DIRECTORY.get, mailer, app_url="x")

    assert summary["failed"] == 1
    assert summary["emails"] == 1
    assert get(table, "a", "2026-10-03", "o3")["remindersSent"] == []
    assert get(table, "b", "2026-10-01", "o1")["remindersSent"] == [1]


@pytest.mark.parametrize("bad", [None, ""])
def test_directory_returning_nothing_is_a_skip(table, bad):
    put_obligation(table, "a", "2026-10-03", "o3")

    summary = run_reminders(table, TODAY, lambda _: bad, FakeMailer(), app_url="x")

    assert summary["skipped"] == 1


def test_subscription_reminders_say_renews_or_trial_ends():
    items = [
        (3, {"kind": "renewal", "vendor": "Netflix", "dueDate": "2026-10-03", "amountCents": 1549}),
        (1, {"kind": "trial_ends", "vendor": "Max", "dueDate": "2026-10-01", "amountCents": 1699}),
    ]

    _, text, _ = render_digest(items, TODAY, app_url="http://localhost:5173")

    assert "Trial ends" in text
    assert "Renews" in text
    assert "Due" not in text  # no unlabelled fallback


def test_past_due_subscriptions_and_bills_roll_forward_each_morning(table):
    from datetime import UTC, datetime

    from reminders.logic import roll_forward
    from subscriptions.store import add_subscription

    now = datetime(2026, 9, 1, tzinfo=UTC)
    add_subscription(
        table, "u1", {"service": "Rent", "category": "bill", "nextRenewal": "2026-09-29"}, now
    )
    add_subscription(
        table, "u1", {"service": "Gym", "nextRenewal": "2026-10-05"}, now
    )  # not due yet

    moved = roll_forward(table, TODAY, now)  # TODAY = Sep 30: Rent's date passed yesterday

    assert moved == 1
    rent = table.get_item(Key={"PK": "USER#u1", "SK": "SUB#rent"})["Item"]
    assert rent["nextRenewal"] == "2026-10-29"


# --- turning reminder emails off, and emailing right away when a reminder starts late --------


def test_people_who_turned_emails_off_get_none_and_nothing_is_claimed(table):
    put_obligation(table, "a", "2026-10-07", "o7")
    set_email_reminders(table, "a", False)
    mailer = FakeMailer()

    summary = run_reminders(table, TODAY, DIRECTORY.get, mailer, app_url="x")

    assert mailer.sent == []
    assert summary["skipped"] == 1
    assert get(table, "a", "2026-10-07", "o7")["remindersSent"] == []  # turning it on resumes


def test_a_reminder_5_days_out_is_emailed_now_and_counts_as_the_7_day_one(table):
    item = put_obligation(table, "a", "2026-10-05", "o5")  # 5 days after TODAY
    mailer = FakeMailer()

    assert send_now(table, item, TODAY, DIRECTORY.get, mailer, "x") == "sent"
    assert [to for to, _ in mailer.sent] == ["a@example.com"]
    assert get(table, "a", "2026-10-05", "o5")["remindersSent"] == [7]
    # The daily run still sends the 3-day and 1-day emails later, never the 7-day one again.
    assert (
        send_now(table, get(table, "a", "2026-10-05", "o5"), TODAY, DIRECTORY.get, mailer, "x")
        == "already_sent"
    )


def test_far_off_reminders_wait_for_the_daily_run(table):
    item = put_obligation(table, "a", "2026-10-20", "o20")
    mailer = FakeMailer()

    assert send_now(table, item, TODAY, DIRECTORY.get, mailer, "x") == "not_soon"
    assert mailer.sent == []


def test_no_immediate_email_when_turned_off(table):
    item = put_obligation(table, "a", "2026-10-02", "o2")
    set_email_reminders(table, "a", False)
    mailer = FakeMailer()

    assert send_now(table, item, TODAY, DIRECTORY.get, mailer, "x") == "turned_off"
    assert mailer.sent == []


def test_immediate_emails_are_capped_per_person_per_day(table):
    mailer = FakeMailer()
    outcomes = [
        send_now(
            table,
            put_obligation(table, "a", "2026-10-02", f"o{i}"),
            TODAY,
            DIRECTORY.get,
            mailer,
            "x",
        )
        for i in range(SEND_NOW_DAILY_CAP + 2)
    ]

    assert outcomes.count("sent") == SEND_NOW_DAILY_CAP
    assert outcomes[-2:] == ["capped", "capped"]  # the daily run still sends the scheduled ones
    assert len(mailer.sent) == SEND_NOW_DAILY_CAP
