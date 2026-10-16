"""Bills typed in by hand, and money someone owes you, saved as ordinary read bills.

A typed bill goes through the same reconcile check a scan does (code computes the total), then
works like any receipt: split it, add it to a trip, ask about it. Money someone owes you is a
one-line bill split entirely to them (`kind: "owed"`), so the paid tick, Remind, "Owed $X", and
Ask OneBill's "who owes me" all work on it unchanged. No model is called.
"""

from datetime import date, datetime, timedelta
from typing import Any

from botocore.exceptions import ClientError

from api.documents import splits
from api.documents.logic import BadRequest, QuotaExceeded, _rechecked, get_document
from documents.reconcile import Line
from shared.ids import new_id
from split.names import MAX_NAME

MAX_ITEMS = 40  # the same as search indexes per bill, so every typed item can be found
MAX_ITEM_CENTS = 10_000_000  # $100,000
MAX_TEXT = 80
DAILY_LIMIT = 30  # typed bills and owed entries per person per day: keeps junk out of the table
USAGE_TTL_DAYS = 7
SELF = "Me"
OWED_VENDOR = "Money you lent"
OWED_LINE = "Loan"
CURRENCY = "USD"  # "Owed to you" adds amounts up: one currency until it totals per currency


def create_bill(table: Any, user_id: str, body: dict[str, Any], now: datetime) -> dict[str, Any]:
    vendor = _text(body.get("vendor"), "Add the store or what it was for.")
    day = _day(body.get("date"), now)
    currency = _currency(body.get("currency"))
    raw_items = body.get("items")
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= MAX_ITEMS:
        raise BadRequest(f"Add 1–{MAX_ITEMS} items.")
    items = [
        (
            _text(
                i.get("description") if isinstance(i, dict) else None, "Every item needs a name."
            ),
            _cents(
                i.get("amountCents") if isinstance(i, dict) else None, "Every item needs a price."
            ),
        )
        for i in raw_items
    ]
    tax = _cents(body.get("taxCents"), "Tax must be an amount.", optional=True)
    tip = _cents(body.get("tipCents"), "Tip must be an amount.", optional=True)
    _count(table, user_id, now)
    return _save(table, user_id, vendor, day, currency, items, tax, tip, now, {})


def create_owed(table: Any, user_id: str, body: dict[str, Any], now: datetime) -> dict[str, Any]:
    raw_name = body.get("name")
    name = " ".join(raw_name.split()) if isinstance(raw_name, str) else ""
    if not 0 < len(name) <= MAX_NAME:
        raise BadRequest(f"Who owes you? Add a name of 1–{MAX_NAME} characters.")
    if name.casefold() == SELF.casefold():
        raise BadRequest("Add the other person's name.")
    amount = _cents(body.get("amountCents"), "How much do they owe you?")
    note = body.get("note")
    note = _text(note, "") if isinstance(note, str) and note.strip() else None
    day = _day(body.get("date"), now)
    currency = _currency(body.get("currency"))
    _count(table, user_id, now)
    doc = _save(
        table,
        user_id,
        note or OWED_VENDOR,
        day,
        currency,
        [(note or OWED_LINE, amount)],
        0,
        0,
        now,
        {"kind": "owed", "person": name},
    )
    try:
        splits.save_split(
            table,
            user_id,
            doc["docId"],
            {
                "members": [{"name": SELF, "self": True}, {"name": name}],
                "assignments": {"1": [1]},
                "charges": {},
                "expectedVersion": 0,
                "expectedUpdatedAt": doc["updatedAt"],
            },
            now,
        )
    except Exception:
        # Without its split it would be a bill nobody owes: don't leave it behind.
        table.delete_item(Key={"PK": f"USER#{user_id}", "SK": f"DOC#{doc['docId']}"})
        raise
    return get_document(table, user_id, doc["docId"], now)


def _save(
    table: Any,
    user_id: str,
    vendor: str,
    day: str,
    currency: str,
    items: list[tuple[str, int]],
    tax: int,
    tip: int,
    now: datetime,
    extra: dict[str, Any],
) -> dict[str, Any]:
    rows: list[Line] = []

    def add(description: str, kind: str, cents: int) -> None:
        rows.append(Line(len(rows) + 1, description, kind, cents))

    for description, cents in items:
        add(description, "item", cents)
    if tax:
        add("Tax", "tax", tax)
    if tip:
        add("Tip", "tip", tip)
    add("Total", "total", sum(c for _, c in items) + tax + tip)
    checked = _rechecked(rows, "receipt")
    stamp = now.isoformat()
    doc_id = new_id(int(now.timestamp() * 1000))
    item = {
        "PK": f"USER#{user_id}",
        "SK": f"DOC#{doc_id}",
        "type": "DOCUMENT",
        "docId": doc_id,
        "source": "manual",
        "docType": "receipt",
        "vendor": vendor,
        "documentDate": day,
        "currency": currency,
        "createdAt": stamp,
        "updatedAt": stamp,
        **checked,
        **extra,
    }
    table.put_item(Item=item, ConditionExpression="attribute_not_exists(PK)")
    return get_document(table, user_id, doc_id, now)


def _count(table: Any, user_id: str, now: datetime) -> None:
    expires = int((now + timedelta(days=USAGE_TTL_DAYS)).timestamp())
    try:
        table.update_item(
            Key={"PK": f"USER#{user_id}", "SK": f"USAGE#{now.date().isoformat()}"},
            UpdateExpression="ADD typed :one SET expiresAt = if_not_exists(expiresAt, :exp)",
            ConditionExpression="attribute_not_exists(typed) OR typed < :limit",
            ExpressionAttributeValues={":one": 1, ":exp": expires, ":limit": DAILY_LIMIT},
        )
    except ClientError as err:
        if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise QuotaExceeded(
                f"You've added {DAILY_LIMIT} bills by hand today. Try again tomorrow."
            ) from err
        raise


def _text(value: Any, message: str) -> str:
    too_long = isinstance(value, str) and len(value) > 4 * MAX_TEXT  # before any work on it
    text = " ".join(value.split()) if isinstance(value, str) and not too_long else ""
    if not 0 < len(text) <= MAX_TEXT:
        raise BadRequest(f"{message} (1–{MAX_TEXT} characters)".strip())
    return text


def _cents(value: Any, message: str, optional: bool = False) -> int:
    if optional and value is None:
        return 0
    lowest = 0 if optional else 1
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not lowest <= value <= MAX_ITEM_CENTS
    ):
        raise BadRequest(message)
    return value


def _day(value: Any, now: datetime) -> str:
    if value is None or value == "":
        return now.date().isoformat()
    try:
        day = date.fromisoformat(str(value))
    except ValueError as err:
        raise BadRequest("Dates look like 2026-10-15.") from err
    if day > now.date() + timedelta(days=1) or day.year < 2000:  # +1: time zones ahead of UTC
        raise BadRequest("Choose a date that isn't in the future.")
    return day.isoformat()


def _currency(value: Any) -> str:
    if value not in (None, CURRENCY):
        raise BadRequest(f"Bills typed in by hand are in {CURRENCY} for now.")
    return CURRENCY
