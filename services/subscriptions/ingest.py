"""The ingest key the Gmail script uses instead of a Cognito sign-in (design S2).

- A user creates it in the app (signed in, `uploaders` only) and sees it once.
- Only its SHA-256 hash is stored: `INGESTKEY#<hash> / KEY → userId`, plus a pointer
  `USER#<id> / INGEST` so creating a new key (or revoking) removes the old one.
- The ingest route has no JWT authorizer: the caller's user comes only from this lookup.
- A daily email cap bounds the model cost if a script ever loops.
"""

import hashlib
import re
import secrets
from datetime import datetime, timedelta
from typing import Any

from botocore.exceptions import ClientError

KEY_PREFIX = "otk_"
UPLOADERS_GROUP = "uploaders"
DAILY_EMAIL_LIMIT = 50
USAGE_TTL_DAYS = 7
_KEY = re.compile(r"^Bearer (otk_[A-Za-z0-9_-]{43})$")


class NotAllowed(Exception):
    pass


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _pointer(user_id: str) -> dict[str, str]:
    return {"PK": f"USER#{user_id}", "SK": "INGEST"}


def _key_item(key_hash: str) -> dict[str, str]:
    return {"PK": f"INGESTKEY#{key_hash}", "SK": "KEY"}


def create_key(table: Any, user_id: str, groups: set[str], now: datetime) -> str:
    """A new key for this user (returned once, never stored); the previous key stops working."""
    if UPLOADERS_GROUP not in groups:
        raise NotAllowed("Connecting Gmail is invite-only for now.")
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    key_hash = _hash(key)
    old = table.get_item(Key=_pointer(user_id)).get("Item")
    ops: list[dict[str, Any]] = [
        {
            "Put": {
                "TableName": table.name,
                "Item": _key_item(key_hash)
                | {"type": "INGEST_KEY", "userId": user_id, "createdAt": now.isoformat()},
                "ConditionExpression": "attribute_not_exists(PK)",
            }
        },
        {
            "Put": {
                "TableName": table.name,
                "Item": _pointer(user_id)
                | {"type": "INGEST", "keyHash": key_hash, "createdAt": now.isoformat()},
                # Two "new key" taps at once can't leave an orphaned key behind.
                **(
                    {"ConditionExpression": "attribute_not_exists(PK)"}
                    if old is None
                    else {
                        "ConditionExpression": "keyHash = :old",
                        "ExpressionAttributeValues": {":old": old["keyHash"]},
                    }
                ),
            }
        },
    ]
    if old:
        ops.append({"Delete": {"TableName": table.name, "Key": _key_item(old["keyHash"])}})
    table.meta.client.transact_write_items(TransactItems=ops)
    return key


def revoke_key(table: Any, user_id: str) -> None:
    old = table.get_item(Key=_pointer(user_id)).get("Item")
    if not old:
        return
    table.meta.client.transact_write_items(
        TransactItems=[
            {"Delete": {"TableName": table.name, "Key": _key_item(old["keyHash"])}},
            {"Delete": {"TableName": table.name, "Key": _pointer(user_id)}},
        ]
    )


def verify_key(table: Any, authorization: str | None) -> str | None:
    """The user this key belongs to, or None. Only the hash is ever looked up."""
    match = _KEY.fullmatch(authorization or "")
    if not match:
        return None
    item = table.get_item(Key=_key_item(_hash(match.group(1)))).get("Item")
    return item["userId"] if item else None


def key_status(table: Any, user_id: str) -> dict[str, Any]:
    item = table.get_item(Key=_pointer(user_id)).get("Item")
    return {
        "connected": item is not None,
        "createdAt": (item or {}).get("createdAt"),
        "lastUsedAt": (item or {}).get("lastUsedAt"),
    }


def record_use(table: Any, user_id: str, now: datetime) -> None:
    """When the script last sent something: shows in Settings that the setup works."""
    try:
        table.update_item(
            Key=_pointer(user_id),
            UpdateExpression="SET lastUsedAt = :now",
            ConditionExpression="attribute_exists(PK)",
            ExpressionAttributeValues={":now": now.isoformat()},
        )
    except ClientError as err:
        if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise


def consume_email_quota(
    table: Any, user_id: str, now: datetime, limit: int = DAILY_EMAIL_LIMIT
) -> bool:
    """Count one email today; False once the daily cap is reached (same USAGE# item as uploads)."""
    expires = int((now + timedelta(days=USAGE_TTL_DAYS)).timestamp())
    try:
        table.update_item(
            Key={"PK": f"USER#{user_id}", "SK": f"USAGE#{now.date().isoformat()}"},
            UpdateExpression="ADD emails :one SET expiresAt = if_not_exists(expiresAt, :exp)",
            ConditionExpression="attribute_not_exists(emails) OR emails < :limit",
            ExpressionAttributeValues={":one": 1, ":exp": expires, ":limit": limit},
        )
    except ClientError as err:
        if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise
    return True
