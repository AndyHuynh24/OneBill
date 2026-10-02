"""Saving subscriptions read from emails (design: docs/design/phase2b-subscriptions.md, S3).

- One `SUB#<serviceKey>` item per service: the latest state plus the last 12 charges.
- Its renewal reminder is a normal `OBL#` item on GSI1, so the existing daily reminders job
  sends it. The reminder is replaced (atomically, with the SUB# write) only when the renewal
  date or kind changes, so reminders already sent aren't repeated.
- `EMAIL#<hash>` markers make processing idempotent: no content, just an outcome, expiring
  after 90 days.
"""

import hashlib
from datetime import date, datetime, timedelta
from typing import Any

from botocore.exceptions import ClientError

from documents.store import obligation_item
from documents.validate import Obligation
from subscriptions.parse import ParsedSubscription

HISTORY_LIMIT = 12
EMAIL_MARKER_DAYS = 90
_HISTORY_STATUSES = frozenset({"charged", "upcoming", "trial_ending"})


class SubscriptionConflict(Exception):
    """Another email for the same service was saved at the same time; retry."""


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
        "createdAt": (existing or {}).get("createdAt", now.isoformat()),
        "updatedAt": now.isoformat(),
    }
    if previous_amount is not None and amount is not None and amount != previous_amount:
        item["previousAmountCents"] = previous_amount
        item["priceChangedAt"] = received.isoformat()
    elif existing and "previousAmountCents" in existing:
        item["previousAmountCents"] = existing["previousAmountCents"]
        item["priceChangedAt"] = existing["priceChangedAt"]

    # The reminder: keep it if nothing changed, otherwise replace it (or remove it).
    kind = "trial_ends" if sub.status == "trial_ending" else "renewal"
    old_key = (existing or {}).get("obligationKey")
    unchanged = (
        old_key
        and existing.get("nextRenewal") == item["nextRenewal"]
        and existing.get("renewalKind") == kind
    )
    deletes, puts = [], []
    if unchanged:
        item["obligationKey"], item["renewalKind"] = old_key, kind
    else:
        if old_key:
            deletes.append(old_key)
        item["obligationKey"], item["renewalKind"] = None, None
        if sub.next_renewal:
            reminder = obligation_item(
                user_id,
                None,
                sub.service,
                Obligation(kind, sub.next_renewal, f"From your {sub.service} email", amount),
                now,
            ) | {"subKey": sub.service_key}
            puts.append(reminder)
            item["obligationKey"], item["renewalKind"] = reminder["SK"], kind

    _transact(table, item, existing, deletes, puts)
    return item


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
    """True if this email should be processed: never seen before, or an earlier try failed."""
    try:
        table.put_item(
            Item=_email_key(user_id, message_id)
            | {
                "type": "EMAIL",
                "status": "processing",
                "createdAt": now.isoformat(),
                "expiresAt": int((now + timedelta(days=EMAIL_MARKER_DAYS)).timestamp()),
            },
            ConditionExpression="attribute_not_exists(SK) OR #s = :failed",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":failed": "failed"},
        )
    except ClientError as err:
        if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise
    return True


def finish_email(
    table: Any, user_id: str, message_id: str, outcome: str, subscriptions: int
) -> None:
    """Record the outcome: processed, ignored (not a subscription), or failed (may be retried)."""
    table.update_item(
        Key=_email_key(user_id, message_id),
        UpdateExpression="SET #s = :o, subscriptions = :n, updatedAt = createdAt",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":o": outcome, ":n": subscriptions},
    )
