"""DOC# state machine and OBL# writes.

pending_upload → processing → ready | needs_review | failed

Every transition is a conditional write on `status`, so a duplicate or retried event can't
process a document twice, and a late write can't overwrite a newer state.
"""

import re
from datetime import datetime, timedelta
from typing import Any

from botocore.exceptions import ClientError

from documents.validate import Obligation, ValidationResult
from shared.ids import is_valid_id, new_id

# Decision R2: OCR pages per day across all users (photos count 1, PDFs their page count).
DAILY_PAGE_BUDGET = 30
USAGE_TTL_DAYS = 7

_UPLOAD_KEY = re.compile(r"^uploads/(?P<user>[A-Za-z0-9-]{1,128})/(?P<doc>[^/]+)$")


class StateConflict(Exception):
    """The document wasn't in the state this write expects."""


def parse_upload_key(key: str) -> tuple[str, str] | None:
    """uploads/<sub>/<docId> → (sub, docId); anything else → None."""
    match = _UPLOAD_KEY.match(key or "")
    if not match or not is_valid_id(match["doc"]):
        return None
    return match["user"], match["doc"]


def _key(user_id: str, doc_id: str) -> dict[str, str]:
    return {"PK": f"USER#{user_id}", "SK": f"DOC#{doc_id}"}


def claim(
    table: Any, user_id: str, doc_id: str, now: datetime, version_id: str | None = None
) -> dict[str, Any] | None:
    """pending_upload → processing. Returns the DOC item, or None if already claimed/unknown.

    The S3 version that triggered the claim is recorded, so later steps read that exact upload.
    """
    try:
        response = table.update_item(
            Key=_key(user_id, doc_id),
            UpdateExpression=(
                "SET #s = :processing, updatedAt = :now, processingStartedAt = :now, "
                "s3VersionId = :version"
            ),
            ConditionExpression="attribute_exists(PK) AND #s = :pending",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":processing": "processing",
                ":pending": "pending_upload",
                ":now": now.isoformat(),
                ":version": version_id,
            },
            ReturnValues="ALL_NEW",
        )
    except ClientError as err:
        if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return None
        raise
    return response["Attributes"]


def consume_page_budget(
    table: Any, pages: int, now: datetime, limit: int = DAILY_PAGE_BUDGET
) -> bool:
    """Reserve `pages` from today's global OCR budget. False (nothing reserved) if it won't fit."""
    if pages > limit:
        return False
    try:
        table.update_item(
            Key={"PK": "GLOBAL", "SK": f"USAGE#{now.date().isoformat()}"},
            UpdateExpression="ADD pages :n SET expiresAt = if_not_exists(expiresAt, :exp)",
            ConditionExpression="attribute_not_exists(pages) OR pages <= :room",
            ExpressionAttributeValues={
                ":n": pages,
                ":room": limit - pages,
                ":exp": int((now + timedelta(days=USAGE_TTL_DAYS)).timestamp()),
            },
        )
    except ClientError as err:
        if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise
    return True


def get(table: Any, user_id: str, doc_id: str) -> dict[str, Any] | None:
    return table.get_item(Key=_key(user_id, doc_id), ConsistentRead=True).get("Item")


def record_textract_job(table: Any, user_id: str, doc_id: str, job_id: str, now: datetime) -> None:
    table.update_item(
        Key=_key(user_id, doc_id),
        UpdateExpression="SET textractJobId = :job, updatedAt = :now",
        ConditionExpression="#s = :processing",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":job": job_id,
            ":now": now.isoformat(),
            ":processing": "processing",
        },
    )


def save_result(
    table: Any,
    user_id: str,
    doc_id: str,
    result: ValidationResult,
    pages: int,
    meta: dict[str, Any],
    now: datetime,
) -> None:
    """Write the DOC result and its OBL# items in one transaction (all or nothing)."""
    key = _key(user_id, doc_id)
    existing = table.get_item(Key=key).get("Item") or {}
    old_keys = existing.get("obligationKeys", [])

    obligation_items = [
        obligation_item(user_id, doc_id, result.vendor, o, now) for o in result.obligations
    ]
    fields = {
        "status": result.status,
        "docType": result.doc_type,
        "vendor": result.vendor,
        "documentDate": result.document_date,
        "currency": result.currency,
        "totalCents": result.total_cents,
        "subtotalCents": result.subtotal_cents,
        "taxCents": result.tax_cents,
        "tipCents": result.tip_cents,
        "feesCents": result.fees_cents,
        "discountsCents": result.discounts_cents,
        "lines": result.lines,
        "corrections": result.corrections,
        "issues": result.issues,
        "notes": result.notes,
        "obligations": [
            {"kind": o["kind"], "dueDate": o["dueDate"], "evidence": o["evidence"]}
            for o in obligation_items
        ],
        "obligationKeys": [o["SK"] for o in obligation_items],
        "pages": pages,
        "processedAt": now.isoformat(),
        "updatedAt": now.isoformat(),
        **meta,
    }
    names = {f"#f{i}": name for i, name in enumerate(fields)}
    values = {f":v{i}": value for i, value in enumerate(fields.values())}
    values[":processing"] = "processing"
    update = {
        "Update": {
            "TableName": table.name,
            "Key": key,
            "UpdateExpression": "SET " + ", ".join(f"#f{i} = :v{i}" for i in range(len(fields))),
            "ConditionExpression": "#status = :processing",
            "ExpressionAttributeNames": names | {"#status": "status"},
            "ExpressionAttributeValues": values,
        }
    }
    deletes = [
        {"Delete": {"TableName": table.name, "Key": {"PK": key["PK"], "SK": sk}}} for sk in old_keys
    ]
    puts = [{"Put": {"TableName": table.name, "Item": item}} for item in obligation_items]
    try:
        table.meta.client.transact_write_items(TransactItems=[update, *deletes, *puts])
    except ClientError as err:
        if err.response["Error"]["Code"] == "TransactionCanceledException":
            raise StateConflict(f"document {doc_id} is not processing") from err
        raise


def mark_failed(table: Any, user_id: str, doc_id: str, reason: str, now: datetime) -> None:
    try:
        table.update_item(
            Key=_key(user_id, doc_id),
            UpdateExpression="SET #s = :failed, failureReason = :reason, updatedAt = :now",
            ConditionExpression="#s IN (:pending, :processing)",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":failed": "failed",
                ":reason": reason,
                ":now": now.isoformat(),
                ":pending": "pending_upload",
                ":processing": "processing",
            },
        )
    except ClientError as err:
        if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise


def obligation_item(
    user_id: str, doc_id: str, vendor: str | None, obligation: Obligation, now: datetime
) -> dict[str, Any]:
    """An OBL# item with its GSI1 keys. Used by extraction and by review corrections."""
    obl_id = new_id(int(now.timestamp() * 1000))
    due = obligation.due_date.isoformat()
    return {
        "PK": f"USER#{user_id}",
        "SK": f"OBL#{due}#{obl_id}",
        "type": "OBLIGATION",
        "oblId": obl_id,
        "docId": doc_id,
        "kind": obligation.type,
        "dueDate": due,
        "vendor": vendor,
        "amountCents": obligation.amount_cents,
        "evidence": obligation.evidence,
        "remindersSent": [],
        "createdAt": now.isoformat(),
        # GSI1: "what's due on this date, for everyone" (daily reminder job)
        "GSI1PK": f"DUE#{due}",
        "GSI1SK": f"USER#{user_id}#OBL#{obl_id}",
    }
