"""Daily reminder digest: what's due in 7, 3, or 1 days, one email per user.

Delivery semantics: each obligation's offset is *claimed* (a conditional write) before the email
is sent, so a retried run can't send it twice; if sending fails, the claim is undone so the next
run tries again.
"""

import html
from collections import defaultdict
from collections.abc import Callable, Iterator
from datetime import date, datetime, timedelta
from typing import Any

from botocore.exceptions import ClientError

from api.me.logic import profile_key, wants_reminder_emails
from shared.money import format_cents
from subscriptions.store import advance_past_renewal

OFFSETS = (7, 3, 1)
KIND_LABELS = {
    "payment_due": "Payment due",
    "warranty_expires": "Warranty expires",
    "renewal": "Renews",
    "trial_ends": "Trial ends",
}

Directory = Callable[[str], str | None]  # user ID → verified email, or None
Mailer = Callable[[str, str, str, str], None]  # (to, subject, text, html)


def collect_due(table: Any, today: date) -> list[tuple[str, int, dict[str, Any]]]:
    """(user_id, offset, obligation item) for every reminder that hasn't been sent yet."""
    found = []
    for offset in OFFSETS:
        due = (today + timedelta(days=offset)).isoformat()
        for item in _query_due(table, due):
            if offset in item.get("remindersSent", []):
                continue
            found.append((item["PK"].removeprefix("USER#"), offset, item))
    return found


def mark_sent(table: Any, item: dict[str, Any], offset: int) -> bool:
    """Claim this reminder. False if another run already did."""
    try:
        table.update_item(
            Key={"PK": item["PK"], "SK": item["SK"]},
            UpdateExpression=(
                "SET remindersSent = list_append(if_not_exists(remindersSent, :empty), :offsets)"
            ),
            ConditionExpression="NOT contains(remindersSent, :offset)",
            ExpressionAttributeValues={":empty": [], ":offsets": [offset], ":offset": offset},
        )
    except ClientError as err:
        if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise
    return True


def unmark_sent(table: Any, item: dict[str, Any], offset: int) -> None:
    """Undo a claim after a failed send, so the next run retries it."""
    key = {"PK": item["PK"], "SK": item["SK"]}
    current = table.get_item(Key=key).get("Item", {}).get("remindersSent", [])
    if offset not in current:
        return
    index = list(current).index(offset)
    try:
        table.update_item(
            Key=key,
            UpdateExpression=f"REMOVE remindersSent[{index}]",
            ConditionExpression=f"remindersSent[{index}] = :offset",
            ExpressionAttributeValues={":offset": offset},
        )
    except ClientError as err:
        if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise


def render_digest(
    items: list[tuple[int, dict[str, Any]]], today: date, app_url: str
) -> tuple[str, str, str]:
    """(subject, plain text, HTML) for one user's reminders, soonest first."""
    ordered = sorted(items, key=lambda entry: (entry[1]["dueDate"], entry[1].get("vendor") or ""))
    count = len(ordered)
    subject = f"OneBill: {count} thing{'s' if count != 1 else ''} due soon"

    rows = []
    for _offset, item in ordered:
        due = date.fromisoformat(item["dueDate"])
        days = (due - today).days
        when = "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"
        label = KIND_LABELS.get(item.get("kind", ""), "Due")
        vendor = item.get("vendor") or "Unknown vendor"
        amount = item.get("amountCents")
        rows.append(
            {
                "label": label,
                "vendor": vendor,
                "date": f"{due:%a}, {due:%b} {due.day} ({when})",
                "amount": format_cents(int(amount)) if amount is not None else None,
            }
        )

    text_lines = ["Here's what's coming up:", ""]
    for row in rows:
        amount = f" · {row['amount']}" if row["amount"] else ""
        text_lines.append(f"• {row['label']}: {row['vendor']}, {row['date']}{amount}")
    text_lines += ["", f"Open OneBill: {app_url}"]

    html_rows = "".join(
        "<li><strong>{label}</strong>: {vendor}, {date}{amount}</li>".format(
            label=html.escape(row["label"]),
            vendor=html.escape(row["vendor"]),
            date=html.escape(row["date"]),
            amount=f" · {html.escape(row['amount'])}" if row["amount"] else "",
        )
        for row in rows
    )
    body_html = (
        "<p>Here's what's coming up:</p>"
        f"<ul>{html_rows}</ul>"
        f'<p><a href="{html.escape(app_url, quote=True)}">Open OneBill</a></p>'
    )
    return subject, "\n".join(text_lines), body_html


def run_reminders(
    table: Any, today: date, directory: Directory, mailer: Mailer, app_url: str
) -> dict[str, int]:
    """Send today's digests. Returns counts only (no addresses), for logging."""
    by_user: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for user_id, offset, item in collect_due(table, today):
        by_user[user_id].append((offset, item))

    summary = {"users": len(by_user), "emails": 0, "items": 0, "skipped": 0, "failed": 0}
    for user_id, entries in by_user.items():
        if not wants_emails(table, user_id):
            summary["skipped"] += 1  # turned off: nothing is claimed, so turning it on resumes
            continue
        email = directory(user_id)
        if not email:
            summary["skipped"] += 1
            continue
        claimed = [(offset, item) for offset, item in entries if mark_sent(table, item, offset)]
        if not claimed:
            continue
        subject, text, body_html = render_digest(claimed, today, app_url)
        try:
            mailer(email, subject, text, body_html)
        except Exception:
            summary["failed"] += 1
            for offset, item in claimed:
                unmark_sent(table, item, offset)
            continue
        summary["emails"] += 1
        summary["items"] += len(claimed)
    return summary


def wants_emails(table: Any, user_id: str) -> bool:
    """The person's setting (on unless they turned it off in the app)."""
    return wants_reminder_emails(table.get_item(Key=profile_key(user_id)).get("Item"))


SEND_NOW_DAILY_CAP = 10  # immediate emails per person per day (each manual add/edit can fire one)


def _take_send_now_slot(table: Any, user_id: str, today: date) -> bool:
    """Count one immediate email for today; False once the day's cap is reached."""
    expires = int(datetime.combine(today + timedelta(days=2), datetime.min.time()).timestamp())
    try:
        table.update_item(
            Key={"PK": f"USER#{user_id}", "SK": f"USAGE#reminder-now#{today.isoformat()}"},
            UpdateExpression="ADD sent :one SET expiresAt = if_not_exists(expiresAt, :expires)",
            ConditionExpression="attribute_not_exists(sent) OR sent < :cap",
            ExpressionAttributeValues={":one": 1, ":cap": SEND_NOW_DAILY_CAP, ":expires": expires},
        )
    except ClientError as err:
        if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise
    return True


def send_now(
    table: Any,
    item: dict[str, Any],
    today: date,
    directory: Directory,
    mailer: Mailer,
    app_url: str,
) -> str:
    """A reminder created already inside the 7-day window gets its email right away.

    It counts as the next scheduled one (5 days away → the 7-day email; 2 days → the 3-day
    one), claimed the same way, so the daily run never repeats it and still sends the later
    ones. Returns what happened, for logging.
    """
    days = (date.fromisoformat(item["dueDate"]) - today).days
    if days < 0 or days > max(OFFSETS):
        return "not_soon"
    offset = min(o for o in OFFSETS if o >= max(days, 1))
    if offset in item.get("remindersSent", []):
        return "already_sent"
    user_id = item["PK"].removeprefix("USER#")
    if not wants_emails(table, user_id):
        return "turned_off"
    email = directory(user_id)
    if not email:
        return "no_email"
    if not _take_send_now_slot(table, user_id, today):
        return "capped"  # the daily run still sends the scheduled ones
    if not mark_sent(table, item, offset):
        return "already_sent"
    subject, text, body_html = render_digest([(offset, item)], today, app_url)
    try:
        mailer(email, subject, text, body_html)
    except Exception:
        unmark_sent(table, item, offset)
        raise
    return "sent"


ROLL_FORWARD_DAYS = 3  # look back a few days, in case a daily run was missed


def roll_forward(table: Any, today: date, now: datetime) -> int:
    """Move subscriptions and bills whose renewal/due date just passed to their next date
    (nobody emails "rent is due again"). Returns how many moved."""
    moved = 0
    for days_ago in range(1, ROLL_FORWARD_DAYS + 1):
        due = (today - timedelta(days=days_ago)).isoformat()
        for item in _query_due(table, due):
            if item.get("subKey"):
                user_id = item["PK"].removeprefix("USER#")
                moved += advance_past_renewal(table, user_id, item["subKey"], today=today, now=now)
    return moved


def _query_due(table: Any, due: str) -> Iterator[dict[str, Any]]:
    kwargs: dict[str, Any] = {
        "IndexName": "GSI1",
        "KeyConditionExpression": "GSI1PK = :due",
        "ExpressionAttributeValues": {":due": f"DUE#{due}"},
    }
    while True:
        response = table.query(**kwargs)
        yield from response.get("Items", [])
        if "LastEvaluatedKey" not in response:
            return
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
