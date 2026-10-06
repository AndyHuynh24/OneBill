"""Saving subscriptions read from emails (design: docs/design/phase2b-subscriptions.md, S3).

- One `SUB#<serviceKey>` item per service: the latest state plus the last 12 charges.
- Its renewal reminder is a normal `OBL#` item on GSI1, so the existing daily reminders job
  sends it. The reminder is replaced (atomically, with the SUB# write) only when the renewal
  date or kind changes, so reminders already sent aren't repeated.
- `EMAIL#<hash>` markers make processing idempotent: no content, just an outcome, expiring
  after 90 days.
"""

import hashlib
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from botocore.exceptions import ClientError

from documents.store import obligation_item
from documents.validate import Obligation
from shared.dates import parse_iso_date
from subscriptions.parse import CYCLES, MAX_TEXT, ParsedSubscription, add_cycle, service_key

HISTORY_LIMIT = 12
EMAIL_MARKER_DAYS = 90
STALE_PROCESSING = timedelta(minutes=5)  # longer than any request can run (API Gateway: 30 s)
_HISTORY_STATUSES = frozenset({"charged", "upcoming", "trial_ending"})


class SubscriptionConflict(Exception):
    """Another email for the same service was saved at the same time; retry."""


class SubscriptionNotFound(Exception):
    pass


class InvalidEdit(ValueError):
    """The message is safe to show to the user."""


class AlreadyTracked(Exception):
    pass


CATEGORIES = ("subscription", "bill")


def _reminder_kind(item: dict[str, Any]) -> str:
    """Bills are "due"; subscriptions "renew"; a trial "ends"."""
    if item.get("status") == "trial_ending":
        return "trial_ends"
    return "payment_due" if item.get("category") == "bill" else "renewal"


def save_subscription(
    table: Any,
    user_id: str,
    sub: ParsedSubscription,
    received: date,
    now: datetime,
    attempts: int = 2,
) -> dict[str, Any]:
    """Merge what this email says into the service's SUB# item and keep its reminder in step."""
    for attempt in range(attempts):
        try:
            return _save_once(table, user_id, sub, received, now)
        except SubscriptionConflict:
            if attempt == attempts - 1:
                raise
    raise AssertionError("unreachable")


def _save_once(
    table: Any, user_id: str, sub: ParsedSubscription, received: date, now: datetime
) -> dict[str, Any]:
    pk, sk = f"USER#{user_id}", f"SUB#{sub.service_key}"
    existing = table.get_item(Key={"PK": pk, "SK": sk}).get("Item")
    history = _with_charge(list((existing or {}).get("history", [])), sub, received)

    # An older email (sent late, or re-sent) only adds to the history; the newest one decides.
    if existing and received.isoformat() < existing.get("lastEmailAt", ""):
        item = existing | {"history": history, "updatedAt": now.isoformat()}
        _transact(table, item, existing, deletes=[], puts=[])
        return item

    previous_amount = (existing or {}).get("amountCents")
    amount = sub.amount_cents if sub.amount_cents is not None else previous_amount
    item: dict[str, Any] = {
        "PK": pk,
        "SK": sk,
        "type": "SUBSCRIPTION",
        "serviceKey": sub.service_key,
        "service": sub.service,
        "plan": sub.plan or (existing or {}).get("plan"),
        "amountCents": amount,
        "currency": sub.currency,
        "cycle": sub.cycle or (existing or {}).get("cycle"),
        "status": sub.status,
        "nextRenewal": sub.next_renewal.isoformat() if sub.next_renewal else None,
        "renewalComputed": sub.renewal_computed,
        "lastChargedAt": (
            sub.charge_date.isoformat()
            if sub.status == "charged" and sub.charge_date
            else (existing or {}).get("lastChargedAt")
        ),
        "lastEmailAt": received.isoformat(),
        "history": history,
        "issues": sub.issues,
        "remindersOff": bool((existing or {}).get("remindersOff", False)),
        "createdAt": (existing or {}).get("createdAt", now.isoformat()),
        "updatedAt": now.isoformat(),
    }
    if previous_amount is not None and amount is not None and amount != previous_amount:
        item["previousAmountCents"] = previous_amount
        item["priceChangedAt"] = received.isoformat()
    elif existing and "previousAmountCents" in existing:
        item["previousAmountCents"] = existing["previousAmountCents"]
        item["priceChangedAt"] = existing["priceChangedAt"]

    item["category"] = (existing or {}).get("category", "subscription")
    deletes, puts = _reminder(
        user_id, item, existing, _reminder_kind(item), f"From your {sub.service} email", now
    )
    _transact(table, item, existing, deletes, puts)
    return item


def _reminder(
    user_id: str,
    item: dict[str, Any],
    existing: dict[str, Any] | None,
    kind: str,
    evidence: str,
    now: datetime,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Keep the renewal reminder if nothing changed; otherwise replace it (or remove it).
    Sets the item's obligationKey/renewalKind; returns the OBL# keys to delete and items to put."""
    old_key = (existing or {}).get("obligationKey")
    if item.get("remindersOff"):  # the user turned reminders off for this subscription
        item["obligationKey"], item["renewalKind"] = None, None
        return ([old_key] if old_key else []), []
    if (
        old_key
        and existing.get("nextRenewal") == item["nextRenewal"]
        and existing.get("renewalKind") == kind
    ):
        item["obligationKey"], item["renewalKind"] = old_key, kind
        return [], []
    deletes = [old_key] if old_key else []
    item["obligationKey"], item["renewalKind"] = None, None
    due = parse_iso_date(item["nextRenewal"]) if item.get("nextRenewal") else None
    if due is None:
        return deletes, []
    reminder = obligation_item(
        user_id,
        None,
        item["service"],
        Obligation(kind, due, evidence, item.get("amountCents")),
        now,
    ) | {"subKey": item["serviceKey"]}
    item["obligationKey"], item["renewalKind"] = reminder["SK"], kind
    return deletes, [reminder]


# --- the app: list, edit, cancel ---------------------------------------------------------------

_KEY = re.compile(r"^[a-z0-9]+( [a-z0-9]+)*$")
_PRIVATE = frozenset({"PK", "SK", "type", "obligationKey", "version"})
MAX_EDIT_CENTS = 10**9


def list_subscriptions(table: Any, user_id: str) -> list[dict[str, Any]]:
    """Active ones first, soonest renewal first; cancelled ones last."""
    response = table.query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
        ExpressionAttributeValues={":pk": f"USER#{user_id}", ":prefix": "SUB#"},
    )
    subs = [_public(item) for item in response.get("Items", [])]
    return sorted(
        subs,
        key=lambda s: (
            s.get("status") == "cancelled",
            s.get("nextRenewal") or "9999",
            s["service"],
        ),
    )


def edit_subscription(
    table: Any, user_id: str, key: str, body: dict[str, Any], now: datetime
) -> dict[str, Any]:
    """Cancel (stops reminders), or correct the amount, next renewal, cycle, or plan."""
    _check_key(key)
    changes = _validated_edit(body)
    for attempt in range(2):
        existing = table.get_item(Key={"PK": f"USER#{user_id}", "SK": f"SUB#{key}"}).get("Item")
        if not existing:
            raise SubscriptionNotFound(key)
        item = (
            dict(existing) | changes | {"updatedAt": now.isoformat(), "editedAt": now.isoformat()}
        )
        if "nextRenewal" in changes:
            item["renewalComputed"] = False
            if existing.get("status") == "cancelled":
                item["status"] = "upcoming"  # a new renewal date means it's active again
        deletes, puts = _reminder(
            user_id, item, existing, _reminder_kind(item), "Edited in the app", now
        )
        try:
            _transact(table, item, existing, deletes, puts)
            return _public(item)
        except SubscriptionConflict:
            if attempt == 1:
                raise
    raise AssertionError("unreachable")


def set_reminders(table: Any, user_id: str, key: str, *, on: bool, now: datetime) -> dict[str, Any]:
    """Turn renewal reminders off (the reminder is removed; later emails stay quiet) or back on."""
    _check_key(key)
    for attempt in range(2):
        existing = table.get_item(Key={"PK": f"USER#{user_id}", "SK": f"SUB#{key}"}).get("Item")
        if not existing:
            raise SubscriptionNotFound(key)
        item = dict(existing) | {"remindersOff": not on, "updatedAt": now.isoformat()}
        kind = "trial_ends" if item.get("status") == "trial_ending" else "renewal"
        deletes, puts = _reminder(
            user_id, item, existing, kind, "From your subscription emails", now
        )
        try:
            _transact(table, item, existing, deletes, puts)
            return _public(item)
        except SubscriptionConflict:
            if attempt == 1:
                raise
    raise AssertionError("unreachable")


def add_subscription(table: Any, user_id: str, body: Any, now: datetime) -> dict[str, Any]:
    """A subscription or bill added by hand (rent, utilities, anything that doesn't email)."""
    fields = _validated_new(body)
    pk, sk = f"USER#{user_id}", f"SUB#{fields['serviceKey']}"
    if table.get_item(Key={"PK": pk, "SK": sk}).get("Item"):
        raise AlreadyTracked(fields["service"])
    item: dict[str, Any] = {
        "PK": pk,
        "SK": sk,
        "type": "SUBSCRIPTION",
        **fields,
        "currency": "USD",
        "status": "upcoming",
        "source": "manual",
        "renewalComputed": False,
        "lastChargedAt": None,
        "lastEmailAt": "",
        "history": [],
        "issues": [],
        "remindersOff": False,
        "createdAt": now.isoformat(),
        "updatedAt": now.isoformat(),
    }
    deletes, puts = _reminder(user_id, item, None, _reminder_kind(item), "Added by hand", now)
    try:
        _transact(table, item, None, deletes, puts)
    except SubscriptionConflict as err:
        raise AlreadyTracked(fields["service"]) from err
    return _public(item)


def advance_past_renewal(table: Any, user_id: str, key: str, *, today: date, now: datetime) -> bool:
    """Move a renewal/due date that has passed to the next one in its cycle (keeping the day of
    the month) and replace its reminder. For bills and subscriptions that don't email."""
    pk = f"USER#{user_id}"
    existing = table.get_item(Key={"PK": pk, "SK": f"SUB#{key}"}).get("Item")
    if not existing or existing.get("status") == "cancelled":
        return False
    anchor = parse_iso_date(existing.get("nextRenewal"))
    cycle = existing.get("cycle")
    if (
        anchor is None
        or anchor >= today
        or cycle not in ("weekly", "monthly", "quarterly", "yearly")
    ):
        return False
    step, due = 0, anchor
    while due < today:
        step += 1
        due = _nth_cycle(anchor, cycle, step)
    item = dict(existing) | {
        "nextRenewal": due.isoformat(),
        "renewalComputed": True,
        "updatedAt": now.isoformat(),
    }
    deletes, puts = _reminder(
        user_id, item, existing, _reminder_kind(item), "Next in its cycle", now
    )
    try:
        _transact(table, item, existing, deletes, puts)
    except SubscriptionConflict:
        return False  # an email updated it meanwhile: that wins
    return True


def _nth_cycle(anchor: date, cycle: str, n: int) -> date:
    """n cycles after the anchor, keeping the anchor's day (Jan 31 → Feb 28 → Mar 31)."""
    if cycle == "weekly":
        return anchor + timedelta(days=7 * n)
    months = {"monthly": 1, "quarterly": 3, "yearly": 12}[cycle] * n
    return add_cycle(anchor, months=months)


def _validated_new(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise InvalidEdit("Send the subscription's details.")
    service = body.get("service")
    if not isinstance(service, str) or not 0 < len(service.strip()) <= MAX_TEXT:
        raise InvalidEdit(f"Give it a name of 1–{MAX_TEXT} characters.")
    service = " ".join(service.split())
    key = service_key(service)
    if not key:
        raise InvalidEdit("The name needs at least one letter or number.")
    category = body.get("category", "subscription")
    if category not in CATEGORIES:
        raise InvalidEdit("category must be subscription or bill.")
    due = body.get("nextRenewal")
    if parse_iso_date(due) is None:
        raise InvalidEdit("Choose the next renewal or due date.")
    cycle = body.get("cycle", "monthly")
    if cycle not in CYCLES:
        raise InvalidEdit(f"cycle must be one of: {', '.join(CYCLES)}.")
    cents = body.get("amountCents")
    if cents is not None and (
        not isinstance(cents, int) or isinstance(cents, bool) or not 0 <= cents <= MAX_EDIT_CENTS
    ):
        raise InvalidEdit("amountCents must be a whole number of cents.")
    plan = body.get("plan")
    if plan is not None and (not isinstance(plan, str) or len(plan.strip()) > MAX_TEXT):
        raise InvalidEdit(f"plan must be at most {MAX_TEXT} characters.")
    return {
        "serviceKey": key,
        "service": service,
        "category": category,
        "nextRenewal": due,
        "cycle": cycle,
        "amountCents": cents,
        "plan": plan.strip() if isinstance(plan, str) and plan.strip() else None,
    }


def delete_subscription(table: Any, user_id: str, key: str) -> None:
    """Remove the subscription, its reminder, and the markers of the emails it came from, so
    those emails are read again if they're sent again (it can come back with new emails)."""
    _check_key(key)
    pk = f"USER#{user_id}"
    existing = table.get_item(Key={"PK": pk, "SK": f"SUB#{key}"}).get("Item")
    if not existing:
        raise SubscriptionNotFound(key)
    ops: list[dict[str, Any]] = [
        {
            "Delete": {
                "TableName": table.name,
                "Key": {"PK": pk, "SK": f"SUB#{key}"},
                "ConditionExpression": "version = :v",
                "ExpressionAttributeValues": {":v": int(existing.get("version", 0))},
            }
        }
    ]
    if existing.get("obligationKey"):
        ops.append(
            {
                "Delete": {
                    "TableName": table.name,
                    "Key": {"PK": pk, "SK": existing["obligationKey"]},
                }
            }
        )
    try:
        table.meta.client.transact_write_items(TransactItems=ops)
    except ClientError as err:
        if err.response["Error"]["Code"] == "TransactionCanceledException":
            raise SubscriptionConflict from err
        raise

    query: dict[str, Any] = {
        "KeyConditionExpression": "PK = :pk AND begins_with(SK, :prefix)",
        "FilterExpression": "contains(subKeys, :key)",
        "ExpressionAttributeValues": {":pk": pk, ":prefix": "EMAIL#", ":key": key},
        "ProjectionExpression": "PK, SK",
    }
    # One DeleteItem per marker (a handful per subscription): the API's role allows DeleteItem
    # but deliberately not BatchWriteItem.
    while True:
        page = table.query(**query)
        for marker in page.get("Items", []):
            table.delete_item(Key={"PK": marker["PK"], "SK": marker["SK"]})
        if "LastEvaluatedKey" not in page:
            break
        query["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def _check_key(key: Any) -> None:
    if not isinstance(key, str) or len(key) > MAX_TEXT or not _KEY.fullmatch(key):
        raise SubscriptionNotFound(key)


def _validated_edit(body: Any) -> dict[str, Any]:
    action = body.get("action") if isinstance(body, dict) else None
    if action == "cancel":
        return {"status": "cancelled", "nextRenewal": None}
    if action != "edit":
        raise InvalidEdit('action must be "cancel" or "edit".')
    fields = body.get("fields")
    allowed = {"amountCents", "nextRenewal", "cycle", "plan"}
    if not isinstance(fields, dict) or not fields or set(fields) - allowed:
        raise InvalidEdit(f"fields must change some of: {', '.join(sorted(allowed))}.")
    out: dict[str, Any] = {}
    if "amountCents" in fields:
        cents = fields["amountCents"]
        if (
            not isinstance(cents, int)
            or isinstance(cents, bool)
            or not 0 <= cents <= MAX_EDIT_CENTS
        ):
            raise InvalidEdit("amountCents must be a whole number of cents.")
        out["amountCents"] = cents
    if "nextRenewal" in fields:
        if parse_iso_date(fields["nextRenewal"]) is None:
            raise InvalidEdit("nextRenewal must be YYYY-MM-DD.")
        out["nextRenewal"] = fields["nextRenewal"]
    if "cycle" in fields:
        if fields["cycle"] not in CYCLES:
            raise InvalidEdit(f"cycle must be one of: {', '.join(CYCLES)}.")
        out["cycle"] = fields["cycle"]
    if "plan" in fields:
        plan = fields["plan"]
        if plan is not None and (not isinstance(plan, str) or len(plan.strip()) > MAX_TEXT):
            raise InvalidEdit(f"plan must be at most {MAX_TEXT} characters.")
        out["plan"] = plan.strip() if isinstance(plan, str) and plan.strip() else None
    return out


def _public(item: dict[str, Any]) -> dict[str, Any]:
    return _plain({k: v for k, v in item.items() if k not in _PRIVATE})


def _plain(value: Any) -> Any:
    """DynamoDB numbers come back as Decimal; money is integer cents."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else str(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _with_charge(
    history: list[dict[str, Any]], sub: ParsedSubscription, received: date
) -> list[dict[str, Any]]:
    if sub.amount_cents is None or sub.status not in _HISTORY_STATUSES:
        return history
    entry = {
        "date": (sub.charge_date or received).isoformat(),
        "amountCents": sub.amount_cents,
        "status": sub.status,
    }
    if not any(
        h["date"] == entry["date"] and h["amountCents"] == entry["amountCents"] for h in history
    ):
        history.append(entry)
    return sorted(history, key=lambda h: h["date"])[-HISTORY_LIMIT:]


def _transact(
    table: Any,
    item: dict[str, Any],
    existing: dict[str, Any] | None,
    deletes: list[str],
    puts: list[dict[str, Any]],
) -> None:
    """Write the SUB# item only if nobody else changed it since it was read (version check)."""
    version = int((existing or {}).get("version", 0))
    put = {
        "Put": {
            "TableName": table.name,
            "Item": item | {"version": version + 1},
            "ConditionExpression": "attribute_not_exists(PK)"
            if existing is None
            else "version = :v",
            **({} if existing is None else {"ExpressionAttributeValues": {":v": version}}),
        }
    }
    ops = [put]
    ops += [
        {"Delete": {"TableName": table.name, "Key": {"PK": item["PK"], "SK": sk}}} for sk in deletes
    ]
    ops += [{"Put": {"TableName": table.name, "Item": obligation}} for obligation in puts]
    try:
        table.meta.client.transact_write_items(TransactItems=ops)
    except ClientError as err:
        if err.response["Error"]["Code"] == "TransactionCanceledException":
            raise SubscriptionConflict from err
        raise


def _email_key(user_id: str, message_id: str) -> dict[str, str]:
    digest = hashlib.sha256(message_id.encode()).hexdigest()[:32]
    return {"PK": f"USER#{user_id}", "SK": f"EMAIL#{digest}"}


def claim_email(table: Any, user_id: str, message_id: str, now: datetime) -> bool:
    """True if this email should be processed: never seen before, an earlier try failed, or an
    earlier try was cut off (still 'processing' long after any request could still be running)."""
    try:
        table.put_item(
            Item=_email_key(user_id, message_id)
            | {
                "type": "EMAIL",
                "status": "processing",
                "createdAt": now.isoformat(),
                "expiresAt": int((now + timedelta(days=EMAIL_MARKER_DAYS)).timestamp()),
            },
            ConditionExpression=(
                "attribute_not_exists(SK) OR #s = :failed"
                " OR (#s = :processing AND createdAt < :stale)"
            ),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":failed": "failed",
                ":processing": "processing",
                ":stale": (now - STALE_PROCESSING).isoformat(),
            },
        )
    except ClientError as err:
        if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise
    return True


def finish_email(
    table: Any,
    user_id: str,
    message_id: str,
    outcome: str,
    subscriptions: int,
    sub_keys: list[str] = (),
) -> None:
    """Record the outcome: processed, ignored (not a subscription), or failed (may be retried).
    `sub_keys` (service keys only, no content) let deleting a subscription forget its emails."""
    table.update_item(
        Key=_email_key(user_id, message_id),
        UpdateExpression="SET #s = :o, subscriptions = :n, subKeys = :k, updatedAt = createdAt",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":o": outcome, ":n": subscriptions, ":k": list(sub_keys)},
    )
