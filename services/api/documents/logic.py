"""Business logic for the documents API. No Lambda or HTTP concepts; tested directly with moto.

Every function takes the caller's user ID from the verified JWT (never from the request) and
builds keys from it, so one user can't reach another user's items.
"""

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from botocore.exceptions import ClientError

from documents.extraction import DEADLINE_TYPES, LINE_KINDS, NO_TOTAL_TYPES
from documents.reconcile import Line, check
from documents.store import obligation_item
from documents.validate import Obligation
from shared.dates import parse_iso_date
from shared.ids import is_valid_id, new_id

UPLOADERS_GROUP = "uploaders"
ALLOWED_TYPES = frozenset({"image/jpeg", "image/png", "application/pdf"})
MAX_BYTES = 10 * 1024 * 1024
MAX_FILENAME = 120
# Decision R2: low enough that the worst case (every limit hit, every day) stays ~$16/month.
USER_DAILY_LIMIT = 10
GLOBAL_DAILY_LIMIT = 15
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
) -> dict[str, Any]:
    """Reserve quota, record a pending DOC item, and return a presigned POST for the browser."""
    if UPLOADERS_GROUP not in groups:
        raise Forbidden("Uploads are invite-only for now.")
    if content_type not in ALLOWED_TYPES:
        raise BadRequest("Upload a JPEG, PNG, or PDF.")
    if not 0 < size_bytes <= MAX_BYTES:
        raise BadRequest("Files must be between 1 byte and 10 MB.")
    display_name = _display_name(filename)
    if not display_name:
        raise BadRequest("A file name is required.")

    day = now.date().isoformat()
    _consume_quota(table, f"USER#{user_id}", day, USER_DAILY_LIMIT, now)
    try:
        _consume_quota(table, "GLOBAL", day, GLOBAL_DAILY_LIMIT, now)
    except QuotaExceeded:
        _refund_quota(table, f"USER#{user_id}", day)
        raise

    doc_id = new_id(int(now.timestamp() * 1000))
    key = f"uploads/{user_id}/{doc_id}"
    timestamp = now.isoformat()
    table.put_item(
        Item={
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
        },
        ConditionExpression="attribute_not_exists(PK)",
    )
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


def _consume_quota(table: Any, pk: str, day: str, limit: int, now: datetime) -> None:
    expires = int((now + timedelta(days=USAGE_TTL_DAYS)).timestamp())
    try:
        table.update_item(
            Key={"PK": pk, "SK": f"USAGE#{day}"},
            UpdateExpression="ADD uploads :one SET expiresAt = if_not_exists(expiresAt, :exp)",
            ConditionExpression="attribute_not_exists(uploads) OR uploads < :limit",
            ExpressionAttributeValues={":one": 1, ":exp": expires, ":limit": limit},
        )
    except ClientError as err:
        if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise QuotaExceeded("Daily upload limit reached. Try again tomorrow.") from err
        raise


def _refund_quota(table: Any, pk: str, day: str) -> None:
    table.update_item(
        Key={"PK": pk, "SK": f"USAGE#{day}"},
        UpdateExpression="ADD uploads :minus",
        ExpressionAttributeValues={":minus": -1},
    )


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
)


def _summary(item: dict[str, Any]) -> dict[str, Any]:
    return _plain({k: item[k] for k in _SUMMARY_ATTRS if k in item})


def review_document(
    table: Any, user_id: str, doc_id: str, body: dict[str, Any], now: datetime
) -> dict[str, Any]:
    """Looks right → accept; Fix → corrected fields (re-validated). Both mark the doc ready,
    except corrected lines: those are re-checked, and the doc stays in review if they don't add up.

    Corrected deadlines replace the document's obligations and their GSI1 entries atomically.
    """
    action = body.get("action")
    if action not in {"accept", "correct"}:
        raise BadRequest('action must be "accept" or "correct".')
    current = get_document(table, user_id, doc_id)  # NotFound for other users' documents
    if current.get("status") not in REVIEWABLE_STATUSES:
        raise BadRequest("Only processed documents can be reviewed.")

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
    else:
        fields = _validated_corrections(body.get("fields"))
        updates["reviewOutcome"] = "corrected"
        updates["correctedFields"] = sorted(fields)
        deadlines = fields.pop("deadlines", None)
        lines = fields.pop("lines", None)
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

    _write_review(
        table, user_id, doc_id, updates, deletes, puts, expected_keys=expected_keys, removes=removes
    )
    return get_document(table, user_id, doc_id, now)


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
        # Ready only when the corrected bill adds up; otherwise the issues say what's off.
        "status": "ready" if rec.ok else "needs_review",
    }


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
