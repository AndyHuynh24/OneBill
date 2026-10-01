"""Business logic for the documents API. No Lambda or HTTP concepts; tested directly with moto.

Every function takes the caller's user ID from the verified JWT (never from the request) and
builds keys from it, so one user can't reach another user's items.
"""

import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from botocore.exceptions import ClientError

from shared.ids import is_valid_id, new_id

UPLOADERS_GROUP = "uploaders"
ALLOWED_TYPES = frozenset({"image/jpeg", "image/png", "application/pdf"})
MAX_BYTES = 10 * 1024 * 1024
MAX_FILENAME = 120
USER_DAILY_LIMIT = 20
GLOBAL_DAILY_LIMIT = 40
UPLOAD_URL_TTL_SECONDS = 300
USAGE_TTL_DAYS = 7
MAX_OBLIGATION_WINDOW_DAYS = 366

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


def list_documents(table: Any, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
    response = table.query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
        ExpressionAttributeValues={":pk": f"USER#{user_id}", ":prefix": "DOC#"},
        ScanIndexForward=False,  # docIds are time-ordered: newest first
        Limit=limit,
    )
    return [_summary(item) for item in response.get("Items", [])]


def get_document(table: Any, user_id: str, doc_id: str) -> dict[str, Any]:
    if not is_valid_id(doc_id):
        raise NotFound("Document not found.")
    item = table.get_item(Key={"PK": f"USER#{user_id}", "SK": f"DOC#{doc_id}"}).get("Item")
    if not item:
        raise NotFound("Document not found.")
    return _public(item)


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
)


def _summary(item: dict[str, Any]) -> dict[str, Any]:
    return _plain({k: item[k] for k in _SUMMARY_ATTRS if k in item})
