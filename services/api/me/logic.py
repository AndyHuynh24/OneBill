"""Pure business logic for GET /me. No Lambda or HTTP concepts here, so it's easy to test."""

from datetime import UTC, datetime
from typing import Any

from botocore.exceptions import ClientError

DEFAULT_BASE_CURRENCY = "USD"


def profile_key(user_id: str) -> dict[str, str]:
    return {"PK": f"USER#{user_id}", "SK": "PROFILE"}


def get_or_create_profile(table: Any, user_id: str, now: datetime | None = None) -> dict[str, str]:
    """Return the caller's PROFILE item, creating it with defaults on first call.

    Creation is idempotent: the put only succeeds if no item exists yet, so two
    concurrent first calls can't overwrite each other; the loser re-reads the winner's item.
    """
    if not user_id or not user_id.strip():
        raise ValueError("user_id is required")

    key = profile_key(user_id)
    # Eventually consistent is fine here (half the cost): a stale miss just falls through to the
    # conditional put, which fails safely and triggers the strongly consistent re-read below.
    item = table.get_item(Key=key).get("Item")
    if item:
        return _to_public(item)

    item = {
        **key,
        "type": "PROFILE",
        "userId": user_id,
        "baseCurrency": DEFAULT_BASE_CURRENCY,
        "createdAt": (now or datetime.now(UTC)).isoformat(),
    }
    try:
        table.put_item(Item=item, ConditionExpression="attribute_not_exists(PK)")
    except ClientError as err:
        if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        item = table.get_item(Key=key, ConsistentRead=True)["Item"]
    return _to_public(item)


def _to_public(item: dict[str, Any]) -> dict[str, str]:
    """Only expose app fields; key attributes (PK/SK) are storage details."""
    return {
        "userId": item["userId"],
        "baseCurrency": item["baseCurrency"],
        "createdAt": item["createdAt"],
    }
