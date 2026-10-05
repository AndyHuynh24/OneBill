"""The ingest key the Gmail script uses instead of a Cognito sign-in (design S2).

- A user creates it in the app (signed in, `owners` only) and sees it once.
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

KEY_PREFIX = "obk_"
OWNERS_GROUP = "owners"  # Connect Gmail is owner-only (emails cost money; inboxes are personal)
DAILY_EMAIL_LIMIT = 10  # per user and day (cost review): labelled by hand, so plenty
GLOBAL_DAILY_EMAIL_LIMIT = 30  # across all users: bounds the worst case even with leaked keys
KEY_LIFETIME = timedelta(days=365)  # a removed user's key stops working within a year
# Every labelled request, before it's remembered: bounds stored markers even for emails that
# never reach the model (e.g. an empty body), per user and day.
DAILY_REQUEST_LIMIT = 200
USAGE_TTL_DAYS = 7
_KEY = re.compile(r"^Bearer (obk_[A-Za-z0-9_-]{43})$")


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
    if OWNERS_GROUP not in groups:
        raise NotAllowed("Connecting Gmail is invite-only for now.")
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    key_hash = _hash(key)
    old = table.get_item(Key=_pointer(user_id)).get("Item")
    ops: list[dict[str, Any]] = [
        {
            "Put": {
                "TableName": table.name,
                "Item": _key_item(key_hash)
                | {
                    "type": "INGEST_KEY",
                    "userId": user_id,
                    "createdAt": now.isoformat(),
                    "expiresOn": (now + KEY_LIFETIME).isoformat(),
                },
                "ConditionExpression": "attribute_not_exists(PK)",
            }
        },
        {
            "Put": {
                "TableName": table.name,
                "Item": _pointer(user_id)
                | {
                    "type": "INGEST",
                    "keyHash": key_hash,
                    "createdAt": now.isoformat(),
                    "expiresOn": (now + KEY_LIFETIME).isoformat(),
                },
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


def verify_key(table: Any, authorization: str | None, now: datetime) -> str | None:
    """The user this key belongs to, or None (unknown or expired). Only the hash is looked up."""
    match = _KEY.fullmatch(authorization or "")
    if not match:
        return None
    item = table.get_item(Key=_key_item(_hash(match.group(1)))).get("Item")
    if not item or _expires_on(item) <= now:
        return None
    return item["userId"]


def _expires_on(item: dict[str, Any]) -> datetime:
    """Keys made before expiry existed last a year from when they were created."""
    if item.get("expiresOn"):
        return datetime.fromisoformat(item["expiresOn"])
    return datetime.fromisoformat(item["createdAt"]) + KEY_LIFETIME


def key_status(table: Any, user_id: str) -> dict[str, Any]:
    item = table.get_item(Key=_pointer(user_id)).get("Item")
    return {
        "connected": item is not None,
        "createdAt": (item or {}).get("createdAt"),
        "expiresOn": _expires_on(item).isoformat() if item else None,
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


def consume_request_quota(
    table: Any, user_id: str, now: datetime, limit: int = DAILY_REQUEST_LIMIT
) -> bool:
    """Count one incoming request today; False once the daily request cap is reached."""
    return _count(table, f"USER#{user_id}", now, "emailRequests", limit)


def consume_email_quota(
    table: Any,
    user_id: str,
    now: datetime,
    limit: int = DAILY_EMAIL_LIMIT,
    global_limit: int = GLOBAL_DAILY_EMAIL_LIMIT,
) -> bool:
    """Count one email today, for this user and for everyone; False once either daily cap is
    reached (same USAGE# items as uploads). A refused email isn't counted against the user."""
    if not _count(table, f"USER#{user_id}", now, "emails", limit):
        return False
    if not _count(table, "GLOBAL", now, "emails", global_limit):
        _uncount(table, f"USER#{user_id}", now, "emails")
        return False
    return True


def _count(table: Any, pk: str, now: datetime, name: str, limit: int) -> bool:
    expires = int((now + timedelta(days=USAGE_TTL_DAYS)).timestamp())
    try:
        table.update_item(
            Key={"PK": pk, "SK": f"USAGE#{now.date().isoformat()}"},
            UpdateExpression="ADD #n :one SET expiresAt = if_not_exists(expiresAt, :exp)",
            ConditionExpression="attribute_not_exists(#n) OR #n < :limit",
            ExpressionAttributeNames={"#n": name},
            ExpressionAttributeValues={":one": 1, ":exp": expires, ":limit": limit},
        )
    except ClientError as err:
        if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise
    return True


def _uncount(table: Any, pk: str, now: datetime, name: str) -> None:
    table.update_item(
        Key={"PK": pk, "SK": f"USAGE#{now.date().isoformat()}"},
        UpdateExpression="ADD #n :minus",
        ConditionExpression="#n > :zero",
        ExpressionAttributeNames={"#n": name},
        ExpressionAttributeValues={":minus": -1, ":zero": 0},
    )
