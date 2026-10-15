"""Business logic for the documents API. No Lambda or HTTP concepts; tested directly with moto.

Every function takes the caller's user ID from the verified JWT (never from the request) and
builds keys from it, so one user can't reach another user's items.
"""

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from botocore.exceptions import ClientError

from api.documents import trip_keys as trips
from documents import store as documents_store
from documents.confidence import after_edit
from documents.extraction import DEADLINE_TYPES, LINE_KINDS, NO_TOTAL_TYPES
from documents.reconcile import Line, check
from documents.store import obligation_item
from documents.validate import Obligation
from shared import usage as app_counts
from shared.dates import parse_iso_date
from shared.ids import is_valid_id, new_id
from subscriptions import ingest as email_ingest
from subscriptions import store as subscriptions

# The app owner (a Cognito group, added by hand): a higher daily cap of their own, and no renewals.
OWNERS_GROUP = "owners"
ALLOWED_TYPES = frozenset({"image/jpeg", "image/png", "application/pdf"})
MAX_BYTES = 10 * 1024 * 1024
MAX_FILENAME = 120
# Uploads per day: anyone signed in gets 3; the owner gets 25.
USER_DAILY_LIMIT = 3
OWNER_DAILY_LIMIT = 25
# Once today's 3 are used, a user can renew them (back to 0) twice a month.
# The owner can't: 25 a day is already theirs.
RENEWALS_PER_MONTH = 2
# Scans per month for the whole app, the owner's included: the only shared limit. At ~2.7¢ a
# scan, it keeps the worst month near the $10 budget.
MONTHLY_APP_LIMIT = 200
MONTHLY_USAGE_TTL_DAYS = 40
UPLOAD_URL_TTL_SECONDS = 300
USAGE_TTL_DAYS = 7
MAX_OBLIGATION_WINDOW_DAYS = 366
# After these, a document can no longer finish on its own: show it as failed, so the app stops
# polling and the user knows to retry. (Display only; the stored status is left untouched.)
STALE_UPLOAD = timedelta(minutes=15)
STALE_PROCESSING = timedelta(minutes=15)
REVIEWABLE_STATUSES = frozenset({"ready", "needs_review"})
EDITABLE_FIELDS = frozenset(
    {"vendor", "documentDate", "totalCents", "currency", "deadlines", "lines"}
)
MAX_LINES = 200
MAX_DESCRIPTION = 120
MAX_VENDOR = 200
MAX_DEADLINES = 10
MAX_ABS_CENTS = 10**11

# Attributes that describe storage, not the document; never returned to clients.
_PRIVATE_ATTRS = frozenset({"PK", "SK", "GSI1PK", "GSI1SK", "GSI2PK", "GSI2SK", "type", "s3Key"})


class DocumentsError(Exception):
    status = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class BadRequest(DocumentsError):
    status = 400


class Forbidden(DocumentsError):
    status = 403


class NotFound(DocumentsError):
    status = 404


class Conflict(DocumentsError):
    status = 409


class QuotaExceeded(DocumentsError):
    status = 429


def parse_groups(claim: Any) -> set[str]:
    """Cognito groups from the access token. HTTP API flattens list claims to "[a b]"."""
    if claim is None:
        return set()
    if isinstance(claim, list):
        return {str(g) for g in claim}
    return set(str(claim).strip("[]").split())


def create_upload(
    table: Any,
    s3: Any,
    bucket: str,
    user_id: str,
    groups: set[str],
    filename: str,
    content_type: str,
    size_bytes: int,
    now: datetime,
    enforce_limits: bool = True,
    trip_id: str | None = None,
) -> dict[str, Any]:
    """Reserve quota, record a pending DOC item, and return a presigned POST for the browser.

    `enforce_limits=False` skips the daily counts (a temporary testing switch); the per-file
    checks always apply. `trip_id`: scanned from a trip's own button, so it joins that trip
    (it must be the caller's, and still on). The main Scan button never joins a trip.
    """
    owner = OWNERS_GROUP in groups
    if content_type not in ALLOWED_TYPES:
        raise BadRequest("Upload a JPEG, PNG, or PDF.")
    if not 0 < size_bytes <= MAX_BYTES:
        raise BadRequest("Files must be between 1 byte and 10 MB.")
    display_name = _display_name(filename)
    if not display_name:
        raise BadRequest("A file name is required.")
    # Checked before any quota is used; re-checked in the write itself (the trip could end).
    if trip_id is not None and not (
        isinstance(trip_id, str)
        and is_valid_id(trip_id)
        and trips.is_active(table, user_id, trip_id)
    ):
        raise BadRequest("That trip isn't on any more. Scan with the main button instead.")

    day = now.date().isoformat()
    if enforce_limits:
        limit = OWNER_DAILY_LIMIT if owner else USER_DAILY_LIMIT
        _consume_quota(table, f"USER#{user_id}", day, limit, now, mine=True)
        try:
            _consume_quota(
                table,
                "GLOBAL",
                _month(now),
                MONTHLY_APP_LIMIT,
                now,
                message="OneBill has reached this month's scan limit. Try again on the 1st.",
                ttl_days=MONTHLY_USAGE_TTL_DAYS,
            )
        except QuotaExceeded:
            _refund_quota(table, f"USER#{user_id}", day)
            raise
    app_counts.count(table, day, "uploads", 1, now, USAGE_TTL_DAYS)  # today's, to watch

    doc_id = new_id(int(now.timestamp() * 1000))
    key = f"uploads/{user_id}/{doc_id}"
    timestamp = now.isoformat()
    item = {
        "PK": f"USER#{user_id}",
        "SK": f"DOC#{doc_id}",
        "type": "DOCUMENT",
        "docId": doc_id,
        "status": "pending_upload",
        "s3Key": key,
        "filename": display_name,
        "contentType": content_type,
        "sizeBytes": size_bytes,
        "createdAt": timestamp,
        "updatedAt": timestamp,
        # The owner's PDFs are counted but never refused by the shared monthly page budget.
        **({"owner": True} if owner else {}),
    }
    # A trip that ended mid-upload: the scan is still saved, just not in the trip.
    if not (trip_id and trips.put_in_trip(table, item, user_id, trip_id)):
        table.put_item(Item=item, ConditionExpression="attribute_not_exists(PK)")
    # A POST policy (unlike a presigned PUT) lets S3 itself refuse oversized or mistyped files.
    post = s3.generate_presigned_post(
        Bucket=bucket,
        Key=key,
        Fields={"Content-Type": content_type},
        Conditions=[{"Content-Type": content_type}, ["content-length-range", 1, MAX_BYTES]],
        ExpiresIn=UPLOAD_URL_TTL_SECONDS,
    )
    return {"docId": doc_id, "upload": {"url": post["url"], "fields": post["fields"]}}


def list_documents(
    table: Any, user_id: str, limit: int = 50, now: datetime | None = None
) -> list[dict[str, Any]]:
    response = table.query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
        ExpressionAttributeValues={":pk": f"USER#{user_id}", ":prefix": "DOC#"},
        ScanIndexForward=False,  # docIds are time-ordered: newest first
        Limit=limit,
    )
    now = now or datetime.now(UTC)
    return [_summary(_effective(item, now)) for item in response.get("Items", [])]


def get_document(
    table: Any, user_id: str, doc_id: str, now: datetime | None = None
) -> dict[str, Any]:
    if not is_valid_id(doc_id):
        raise NotFound("Document not found.")
    item = table.get_item(Key={"PK": f"USER#{user_id}", "SK": f"DOC#{doc_id}"}).get("Item")
    if not item:
        raise NotFound("Document not found.")
    return _public(_effective(item, now or datetime.now(UTC)))


def _effective(item: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Report documents that can no longer finish as failed (abandoned upload, stuck run)."""
    status = item.get("status")
    if status == "pending_upload" and _older_than(item.get("createdAt"), now, STALE_UPLOAD):
        return item | {
            "status": "failed",
            "failureReason": "The upload didn't finish. Please try again.",
        }
    if status == "processing" and _older_than(
        item.get("processingStartedAt"), now, STALE_PROCESSING
    ):
        return item | {
            "status": "failed",
            "failureReason": "Reading this document took too long. Please upload it again.",
        }
    return item


def _older_than(timestamp: Any, now: datetime, age: timedelta) -> bool:
    try:
        return now - datetime.fromisoformat(str(timestamp)) > age
    except ValueError:
        return False


def list_obligations(
    table: Any, user_id: str, today: date, within_days: int
) -> list[dict[str, Any]]:
    if not 0 <= within_days <= MAX_OBLIGATION_WINDOW_DAYS:
        raise BadRequest(f"withinDays must be between 0 and {MAX_OBLIGATION_WINDOW_DAYS}.")
    last = today + timedelta(days=within_days - 1) if within_days else today
    response = table.query(
        KeyConditionExpression="PK = :pk AND SK BETWEEN :start AND :end",
        ExpressionAttributeValues={
            ":pk": f"USER#{user_id}",
            ":start": f"OBL#{today.isoformat()}",
            ":end": f"OBL#{last.isoformat()}~",  # "~" sorts after every "#<id>" suffix
        },
    )
    return [_public(item) for item in response.get("Items", [])]


def _display_name(filename: str) -> str:
    name = re.split(r"[\\/]", filename or "")[-1].strip()
    name = re.sub(r"[^\w .()-]", "_", name)
    if len(name) > MAX_FILENAME:
        stem, dot, ext = name.rpartition(".")
        keep = MAX_FILENAME - len(ext) - 1 if dot and len(ext) <= 5 else MAX_FILENAME
        name = f"{stem[:keep]}.{ext}" if dot and len(ext) <= 5 else name[:MAX_FILENAME]
    return name


def _consume_quota(
    table: Any,
    pk: str,
    day: str,
    limit: int,
    now: datetime,
    mine: bool = False,
    message: str | None = None,
    ttl_days: int = USAGE_TTL_DAYS,
) -> None:
    expires = int((now + timedelta(days=ttl_days)).timestamp())
    try:
        table.update_item(
            Key={"PK": pk, "SK": f"USAGE#{day}"},
            UpdateExpression="ADD uploads :one SET expiresAt = if_not_exists(expiresAt, :exp)",
            ConditionExpression="attribute_not_exists(uploads) OR uploads < :limit",
            ExpressionAttributeValues={":one": 1, ":exp": expires, ":limit": limit},
        )
    except ClientError as err:
        if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise QuotaExceeded(
                message
                or (
                    f"You've used today's {limit} uploads. Try again tomorrow."
                    if mine
                    else "OneBill has reached today's upload limit. Try again tomorrow."
                )
            ) from err
        raise


def _refund_quota(table: Any, pk: str, day: str) -> None:
    try:
        table.update_item(
            Key={"PK": pk, "SK": f"USAGE#{day}"},
            UpdateExpression="ADD uploads :minus",
            # A renewal may have reset the count meanwhile: never go below zero.
            ConditionExpression="uploads > :zero",
            ExpressionAttributeValues={":minus": -1, ":zero": 0},
        )
    except ClientError as err:
        if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise


def _month(now: datetime) -> str:
    return now.strftime("%Y-%m")


def _usage_item(table: Any, pk: str, period: str) -> dict[str, Any]:
    key = {"PK": pk, "SK": f"USAGE#{period}"}
    return table.get_item(Key=key, ConsistentRead=True).get("Item") or {}


def get_usage(table: Any, user_id: str, groups: set[str], now: datetime) -> dict[str, int]:
    """The caller's scans today and renewals left this month (the Scan button's line)."""
    owner = OWNERS_GROUP in groups
    limit = OWNER_DAILY_LIMIT if owner else USER_DAILY_LIMIT
    used = int(_usage_item(table, f"USER#{user_id}", now.date().isoformat()).get("uploads", 0))
    renewed = int(_usage_item(table, f"USER#{user_id}", _month(now)).get("renewals", 0))
    return {
        "dailyLimit": limit,
        "usedToday": used,
        "leftToday": max(0, limit - used),
        "renewalsLeft": 0 if owner else max(0, RENEWALS_PER_MONTH - renewed),
    }


def renew_usage(table: Any, user_id: str, groups: set[str], now: datetime) -> dict[str, int]:
    """Give back today's scans once they're all used: at most twice a month, never for the owner.

    One transaction, so a double tap can't renew twice: today's count goes back to 0 only if it
    reached the limit, and the month's renewals go up only while under the cap.
    """
    if OWNERS_GROUP in groups:
        raise Conflict("Your daily scans can't be renewed.")
    pk, day, month = f"USER#{user_id}", now.date().isoformat(), _month(now)
    month_ttl = int((now + timedelta(days=MONTHLY_USAGE_TTL_DAYS)).timestamp())
    ops = [
        {
            "Update": {
                "TableName": table.name,
                "Key": {"PK": pk, "SK": f"USAGE#{day}"},
                "UpdateExpression": "SET uploads = :zero",
                "ConditionExpression": "uploads >= :limit",
                "ExpressionAttributeValues": {":zero": 0, ":limit": USER_DAILY_LIMIT},
            }
        },
        {
            "Update": {
                "TableName": table.name,
                "Key": {"PK": pk, "SK": f"USAGE#{month}"},
                "UpdateExpression": (
                    "ADD renewals :one SET expiresAt = if_not_exists(expiresAt, :exp)"
                ),
                "ConditionExpression": "attribute_not_exists(renewals) OR renewals < :max",
                "ExpressionAttributeValues": {
                    ":one": 1,
                    ":exp": month_ttl,
                    ":max": RENEWALS_PER_MONTH,
                },
            }
        },
    ]
    try:
        table.meta.client.transact_write_items(TransactItems=ops)
    except ClientError as err:
        if err.response["Error"]["Code"] != "TransactionCanceledException":
            raise
        reasons = [r.get("Code") for r in err.response.get("CancellationReasons", [])]
        if reasons[:1] == ["ConditionalCheckFailed"]:
            raise Conflict("You still have scans left today.") from err
        if reasons[1:2] == ["ConditionalCheckFailed"]:
            raise Conflict(
                f"You've used this month's {RENEWALS_PER_MONTH} renewals. Scans come back tomorrow."
            ) from err
        raise Conflict("Renewing didn't go through. Please try again.") from err
    # The owner's App usage card: outside the transaction, so these shared items never conflict.
    app_counts.count(table, day, "renewals", 1, now, USAGE_TTL_DAYS)
    app_counts.count(table, month, "renewals", 1, now, MONTHLY_USAGE_TTL_DAYS)
    return get_usage(table, user_id, groups, now)


HISTORY_DAYS = 7  # the day counters expire after USAGE_TTL_DAYS, so no more than that


def app_usage(table: Any, now: datetime) -> dict[str, Any]:
    """App-wide counts for the last 7 days and this month, with the monthly limits (owner only).

    Totals only: no user IDs or emails.
    """
    names = {"scans": "uploads", "pages": "pages", "emails": "emails", "renewals": "renewals"}

    def totals(item: dict[str, Any]) -> dict[str, int]:
        return {label: int(item.get(attr, 0)) for label, attr in names.items()}

    today = now.date()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(HISTORY_DAYS)]
    # One query for the week. The month item ("USAGE#2026-09") can sort inside the range, so
    # only exact dates are kept.
    response = table.query(
        KeyConditionExpression="PK = :pk AND SK BETWEEN :first AND :last",
        ExpressionAttributeValues={
            ":pk": "GLOBAL",
            ":first": f"USAGE#{dates[-1]}",
            ":last": f"USAGE#{dates[0]}",
        },
        ConsistentRead=True,
    )
    by_date = {item["SK"].removeprefix("USAGE#"): item for item in response.get("Items", [])}
    days = [{"date": d, **totals(by_date.get(d, {}))} for d in dates]
    return {
        "today": {k: v for k, v in days[0].items() if k != "date"},
        "days": days,
        "month": totals(_usage_item(table, "GLOBAL", _month(now))),
        "limits": {
            "scans": MONTHLY_APP_LIMIT,
            "pages": documents_store.MONTHLY_PAGE_BUDGET,
            "emails": email_ingest.GLOBAL_MONTHLY_EMAIL_LIMIT,
        },
    }


def _plain(value: Any) -> Any:
    """DynamoDB numbers come back as Decimal; money is integer cents, so return ints."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else str(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _public(item: dict[str, Any]) -> dict[str, Any]:
    return _plain({k: v for k, v in item.items() if k not in _PRIVATE_ATTRS})


_SUMMARY_ATTRS = (
    "docId",
    "status",
    "filename",
    "contentType",
    "createdAt",
    "updatedAt",
    "vendor",
    "documentDate",
    "totalCents",
    "currency",
    "reviewOutcome",
    "failureReason",
    "source",  # "email" for labelled emails read like a scan
    "confidence",  # {"level", "checks"}: the list shows a ✓ or "Check" badge
    "tripId",  # in a trip: settled on the trip, not per receipt
)


def _summary(item: dict[str, Any]) -> dict[str, Any]:
    summary = _plain({k: item[k] for k in _SUMMARY_ATTRS if k in item})
    if item.get("split") and not item.get("tripId"):  # a trip settles as a whole
        summary["owedCents"] = _owed_cents(item["split"])
    return summary


def _owed_cents(split: dict[str, Any]) -> int:
    """What the others on a split still owe you: their shares, minus anyone ticked as paid."""
    others = {m["id"] for m in split.get("members", []) if not m.get("self")}
    paid = split.get("paid") or {}
    return sum(
        int(s["totalCents"])
        for s in split.get("shares", [])
        if s["member"] in others and s["member"] not in paid
    )


def review_document(
    table: Any, user_id: str, doc_id: str, body: dict[str, Any], now: datetime
) -> dict[str, Any]:
    """Looks right → accept; Fix → corrected fields (re-validated). Both mark the doc ready,
    except corrected lines: those are re-checked, and the doc stays in review if they don't add up.

    Corrected deadlines replace the document's obligations and their GSI1 entries atomically.
    """
    action = body.get("action")
    if action not in {"accept", "correct", "confirm"}:
        raise BadRequest('action must be "accept", "correct", or "confirm".')
    current = get_document(table, user_id, doc_id)  # NotFound for other users' documents
    if current.get("status") not in REVIEWABLE_STATUSES:
        raise BadRequest("Only processed documents can be reviewed.")
    if action == "confirm":
        return _confirm_lines(table, user_id, doc_id, current, body.get("rows"), now)

    updates: dict[str, Any] = {
        "status": "ready",
        "reviewedAt": now.isoformat(),
        "updatedAt": now.isoformat(),
    }
    deletes: list[str] = []
    puts: list[dict[str, Any]] = []
    removes: list[str] = []
    # The deadlines as they were when this review was read; the write only applies if they're
    # unchanged (a concurrent save, e.g. a double-click, can't orphan obligations).
    expected_keys = _current_keys(table, user_id, doc_id)
    if action == "accept":
        updates["reviewOutcome"] = "accepted"
        updates["confidence"] = {"level": "confirmed", "checks": []}  # the user checked it all
    else:
        fields = _validated_corrections(body.get("fields"))
        updates["reviewOutcome"] = "corrected"
        updates["correctedFields"] = sorted(fields)
        deadlines = fields.pop("deadlines", None)
        lines = fields.pop("lines", None)
        if "totalCents" in fields:
            removes.append("split")  # shares were computed for the old total
        updates.update(fields)
        if lines is not None:
            updates.update(_rechecked(lines, current.get("docType")))
            removes.append("split")  # rows may have changed: an old split can't be trusted
        if deadlines is not None:
            vendor = fields.get("vendor", current.get("vendor"))
            amount = fields.get("totalCents", current.get("totalCents"))
            puts = [
                obligation_item(
                    user_id,
                    doc_id,
                    vendor,
                    Obligation(kind, due, "Entered during review", amount),
                    now,
                )
                for kind, due in deadlines
            ]
            deletes = expected_keys
            updates["obligationKeys"] = [item["SK"] for item in puts]
            updates["obligations"] = [
                {"kind": item["kind"], "dueDate": item["dueDate"], "evidence": item["evidence"]}
                for item in puts
            ]
        if lines is not None:
            # Edited lines are trusted; flags on untouched lines stay; if it still doesn't add
            # up, fresh one-digit suggestions are offered. Pure math: no OCR, no model.
            updates["confidence"] = after_edit(
                _plain(current.get("confidence") or {}),
                _stored_lines(current),
                lines,
                total_required=current.get("docType") not in NO_TOTAL_TYPES,
            )

    _write_review(
        table, user_id, doc_id, updates, deletes, puts, expected_keys=expected_keys, removes=removes
    )
    return get_document(table, user_id, doc_id, now)


def remove_obligation(table: Any, user_id: str, obl_id: str, now: datetime) -> None:
    """× on a reminder. A document's deadline is removed from the document too; a
    subscription's renewal turns that subscription's reminders off (so the next email doesn't
    bring it back)."""
    if not is_valid_id(obl_id):
        raise NotFound("Reminder not found.")
    pk = f"USER#{user_id}"
    found = table.query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
        FilterExpression="oblId = :id",
        ExpressionAttributeValues={":pk": pk, ":prefix": "OBL#", ":id": obl_id},
    )["Items"]
    if not found:
        raise NotFound("Reminder not found.")
    item = found[0]
    if item.get("subKey"):
        subscriptions.set_reminders(table, user_id, item["subKey"], on=False, now=now)
        return
    if not item.get("docId"):
        table.delete_item(Key={"PK": pk, "SK": item["SK"]})
        return
    doc = table.get_item(Key={"PK": pk, "SK": f"DOC#{item['docId']}"}).get("Item") or {}
    keys = list(doc.get("obligationKeys", []))
    listed = list(doc.get("obligations", []))
    kept = [(k, o) for k, o in zip(keys, listed, strict=False) if k != item["SK"]]
    ops: list[dict[str, Any]] = [
        {"Delete": {"TableName": table.name, "Key": {"PK": pk, "SK": item["SK"]}}}
    ]
    if doc:
        ops.append(
            {
                "Update": {
                    "TableName": table.name,
                    "Key": {"PK": pk, "SK": f"DOC#{item['docId']}"},
                    "UpdateExpression": (
                        "SET obligationKeys = :keys, obligations = :listed, updatedAt = :now"
                    ),
                    # Only if no other save changed the deadlines since they were read.
                    "ConditionExpression": "obligationKeys = :expected",
                    "ExpressionAttributeValues": {
                        ":keys": [k for k, _ in kept],
                        ":listed": [o for _, o in kept],
                        ":now": now.isoformat(),
                        ":expected": keys,
                    },
                }
            }
        )
    try:
        table.meta.client.transact_write_items(TransactItems=ops)
    except ClientError as err:
        if err.response["Error"]["Code"] == "TransactionCanceledException":
            raise BadRequest("This document changed. Reload and try again.") from err
        raise


def delete_document(
    table: Any, s3: Any, bucket: str, user_id: str, doc_id: str, now: datetime
) -> None:
    """Delete a receipt or bill: its item, its deadlines, and its photo or PDF."""
    if not is_valid_id(doc_id):
        raise NotFound("Document not found.")
    pk = f"USER#{user_id}"
    item = table.get_item(Key={"PK": pk, "SK": f"DOC#{doc_id}"}).get("Item")
    if not item:
        raise NotFound("Document not found.")
    if _effective(item, now).get("status") == "processing":
        raise BadRequest("We're still reading this document. Try again in a minute.")
    keys = list(item.get("obligationKeys", []))
    # Only if its deadlines are still the ones read here: a concurrent edit can't leave
    # reminders behind that keep emailing about a deleted bill.
    unchanged = (
        "obligationKeys = :keys"
        if keys
        else "attribute_not_exists(obligationKeys) OR obligationKeys = :keys"
    )
    ops = [
        {
            "Delete": {
                "TableName": table.name,
                "Key": {"PK": pk, "SK": f"DOC#{doc_id}"},
                "ConditionExpression": unchanged,
                "ExpressionAttributeValues": {":keys": keys},
            }
        }
    ]
    ops += [{"Delete": {"TableName": table.name, "Key": {"PK": pk, "SK": sk}}} for sk in keys]
    try:
        table.meta.client.transact_write_items(TransactItems=ops)
    except ClientError as err:
        if err.response["Error"]["Code"] == "TransactionCanceledException":
            raise BadRequest("This bill changed. Reload and try again.") from err
        raise
    # Versioned bucket: this adds a delete marker; the old version expires after 30 days.
    s3.delete_object(Bucket=bucket, Key=item.get("s3Key") or f"uploads/{user_id}/{doc_id}")


def review_stats(documents: list[dict[str, Any]]) -> dict[str, Any]:
    """Acceptance rate: share of reviewed documents the user accepted without changes."""
    reviewed = [d for d in documents if d.get("reviewOutcome")]
    accepted = sum(1 for d in reviewed if d["reviewOutcome"] == "accepted")
    rate = round(accepted / len(reviewed), 2) if reviewed else None
    return {"reviewed": len(reviewed), "acceptedUnchanged": accepted, "acceptanceRate": rate}


def _rechecked(lines: list[Line], doc_type: str | None) -> dict[str, Any]:
    """Corrected lines become the document's lines; totals and status follow from the math."""
    rec = check(lines, total_required=doc_type not in NO_TOTAL_TYPES)
    return {
        "lines": [
            {
                "row": line.row,
                "description": line.description,
                "kind": line.kind,
                "amountCents": line.amount_cents,
            }
            for line in rec.lines
        ],
        "totalCents": rec.total_cents,
        "subtotalCents": rec.printed_subtotal_cents,
        "taxCents": rec.tax_cents,
        "tipCents": rec.tip_cents,
        "feesCents": rec.fees_cents,
        "discountsCents": rec.discounts_cents,
        "corrections": [],  # the person's labels replace any automatic relabelling
        "issues": rec.issues,
        "notes": rec.notes,
        "taxIncluded": rec.tax_included,
        "math": rec.math,
        # Ready only when the corrected bill adds up; otherwise the issues say what's off.
        "status": "ready" if rec.ok else "needs_review",
    }


def _stored_lines(item: dict[str, Any]) -> list[Line]:
    return [
        Line(int(x["row"]), x["description"], x["kind"], int(x["amountCents"]))
        for x in item.get("lines", [])
    ]


def _validated_lines(raw: Any) -> list[Line]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_LINES:
        raise BadRequest(f"lines must be a list of 1–{MAX_LINES} lines.")
    lines = []
    for row, entry in enumerate(raw, start=1):  # rows are numbered here, never by the client
        if not isinstance(entry, dict):
            raise BadRequest(f"Line {row} must be an object.")
        description, kind, cents = (
            entry.get("description"),
            entry.get("kind"),
            entry.get("amountCents"),
        )
        if not isinstance(description, str) or not 0 < len(description.strip()) <= MAX_DESCRIPTION:
            raise BadRequest(f"Line {row} needs a description of 1–{MAX_DESCRIPTION} characters.")
        if kind not in LINE_KINDS:
            raise BadRequest(f"Line {row} has an unknown kind.")
        if not isinstance(cents, int) or isinstance(cents, bool) or abs(cents) > MAX_ABS_CENTS:
            raise BadRequest(f"Line {row} needs a whole number of cents.")
        lines.append(Line(row=row, description=description.strip(), kind=kind, amount_cents=cents))
    return lines


def _current_keys(table: Any, user_id: str, doc_id: str) -> list[str]:
    item = table.get_item(Key={"PK": f"USER#{user_id}", "SK": f"DOC#{doc_id}"}).get("Item") or {}
    return list(item.get("obligationKeys", []))


def _validated_corrections(fields: Any) -> dict[str, Any]:
    if not isinstance(fields, dict) or not fields:
        raise BadRequest("fields must be a non-empty object.")
    unknown = set(fields) - EDITABLE_FIELDS
    if unknown:
        raise BadRequest(f"These fields can't be edited: {', '.join(sorted(unknown))}.")
    out: dict[str, Any] = {}
    if "vendor" in fields:
        vendor = fields["vendor"]
        if vendor is not None and (
            not isinstance(vendor, str) or not 0 < len(vendor.strip()) <= MAX_VENDOR
        ):
            raise BadRequest(f"vendor must be 1–{MAX_VENDOR} characters.")
        out["vendor"] = vendor.strip() if isinstance(vendor, str) else None
    if "documentDate" in fields:
        value = fields["documentDate"]
        if value is not None and parse_iso_date(value) is None:
            raise BadRequest("documentDate must be YYYY-MM-DD.")
        out["documentDate"] = value
    if "totalCents" in fields:
        value = fields["totalCents"]
        if not isinstance(value, int) or isinstance(value, bool) or abs(value) > MAX_ABS_CENTS:
            raise BadRequest("totalCents must be a whole number of cents.")
        out["totalCents"] = value
    if "currency" in fields:
        value = fields["currency"]
        if (
            not isinstance(value, str)
            or len(value) != 3
            or not value.isalpha()
            or not value.isupper()
        ):
            raise BadRequest("currency must be a 3-letter code like USD.")
        out["currency"] = value
    if "lines" in fields:
        if "totalCents" in fields:
            raise BadRequest("When editing lines, change the total line instead of totalCents.")
        out["lines"] = _validated_lines(fields["lines"])
    if "deadlines" in fields:
        raw = fields["deadlines"]
        if not isinstance(raw, list) or len(raw) > MAX_DEADLINES:
            raise BadRequest(f"deadlines must be a list of at most {MAX_DEADLINES}.")
        deadlines = []
        for entry in raw:
            kind = entry.get("kind") if isinstance(entry, dict) else None
            due = parse_iso_date(entry.get("dueDate")) if isinstance(entry, dict) else None
            if kind not in DEADLINE_TYPES or due is None:
                raise BadRequest("Each deadline needs a valid kind and a YYYY-MM-DD dueDate.")
            deadlines.append((kind, due))
        out["deadlines"] = deadlines
    return out


MAX_CONFIRM_ROWS = 200


def _confirm_lines(
    table: Any,
    user_id: str,
    doc_id: str,
    current: dict[str, Any],
    rows: Any,
    now: datetime,
) -> dict[str, Any]:
    """'Looks right' on flagged lines: clears those flags. With none left, a bill whose totals
    add up counts as checked by the user. The status never changes here."""
    valid = (
        isinstance(rows, list)
        and 0 < len(rows) <= MAX_CONFIRM_ROWS
        and all(isinstance(r, int) and not isinstance(r, bool) for r in rows)
    )
    if not valid:
        raise BadRequest(f"rows must be a list of 1–{MAX_CONFIRM_ROWS} line numbers.")
    before = _plain(current.get("confidence") or {"level": "check", "checks": []})
    remaining = [c for c in before.get("checks", []) if int(c["row"]) not in set(rows)]
    if remaining or current.get("status") != "ready":
        level = before.get("level", "check") if remaining else "check"
    else:
        level = "confirmed"
    try:
        table.update_item(
            Key={"PK": f"USER#{user_id}", "SK": f"DOC#{doc_id}"},
            UpdateExpression="SET confidence = :new, updatedAt = :now",
            # Only if nothing else changed the flags since they were read (no lost confirms).
            ConditionExpression="attribute_exists(PK) AND "
            "(attribute_not_exists(confidence) OR confidence = :old)",
            ExpressionAttributeValues={
                ":new": {"level": level, "checks": remaining},
                ":old": current.get("confidence") or {},
                ":now": now.isoformat(),
            },
        )
    except ClientError as err:
        if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise BadRequest("This bill changed. Reload and try again.") from err
        raise
    return get_document(table, user_id, doc_id)


def _write_review(
    table: Any,
    user_id: str,
    doc_id: str,
    updates: dict[str, Any],
    delete_keys: list[str],
    puts: list[dict[str, Any]],
    expected_keys: list[str],
    removes: list[str] = (),
) -> None:
    pk = f"USER#{user_id}"
    names = {f"#f{i}": name for i, name in enumerate(updates)}
    names |= {f"#r{i}": name for i, name in enumerate(removes)}
    remove = (" REMOVE " + ", ".join(f"#r{i}" for i in range(len(removes)))) if removes else ""
    values = {f":v{i}": value for i, value in enumerate(updates.values())}
    values |= {":ready": "ready", ":review": "needs_review", ":expected": expected_keys}
    unchanged = "obligationKeys = :expected"
    if not expected_keys:
        unchanged = f"(attribute_not_exists(obligationKeys) OR {unchanged})"
    update = {
        "Update": {
            "TableName": table.name,
            "Key": {"PK": pk, "SK": f"DOC#{doc_id}"},
            "UpdateExpression": "SET "
            + ", ".join(f"#f{i} = :v{i}" for i in range(len(updates)))
            + remove,
            # Only processed documents change here, and only if no other save changed their
            # deadlines since this review read them.
            "ConditionExpression": f"#status IN (:ready, :review) AND {unchanged}",
            "ExpressionAttributeNames": names | {"#status": "status"},
            "ExpressionAttributeValues": values,
        }
    }
    deletes = [
        {"Delete": {"TableName": table.name, "Key": {"PK": pk, "SK": sk}}} for sk in delete_keys
    ]
    put_items = [{"Put": {"TableName": table.name, "Item": item}} for item in puts]
    try:
        table.meta.client.transact_write_items(TransactItems=[update, *deletes, *put_items])
    except ClientError as err:
        if err.response["Error"]["Code"] == "TransactionCanceledException":
            raise BadRequest(
                "This document changed while you were reviewing it. Reload and try again."
            ) from err
        raise
