"""Trip keys shared by uploads and trips (kept apart from trips.py to avoid an import cycle)."""

from typing import Any

from botocore.exceptions import ClientError

ACTIVE_SK = "ACTIVE_TRIP"


def trip_gsi_keys(user_id: str, trip_id: str, doc_id: str) -> dict[str, str]:
    """Sparse GSI2 keys for a receipt in a trip; the user ID in the key scopes the query."""
    return {"GSI2PK": f"USER#{user_id}#TRIP#{trip_id}", "GSI2SK": f"DOC#{doc_id}"}


def is_active(table: Any, user_id: str, trip_id: str) -> bool:
    """The caller's own trip, and still on (a scan from its tile may join it)."""
    item = table.get_item(Key={"PK": f"USER#{user_id}", "SK": f"TRIP#{trip_id}"}).get("Item")
    return bool(item) and item.get("status") == "active"


def put_in_trip(table: Any, item: dict[str, Any], user_id: str, trip_id: str) -> bool:
    """Put a new DOC in a trip only if that trip is still on (one transaction, so a trip ended
    mid-upload can't gain a receipt). False: the trip ended; nothing was written."""
    try:
        table.meta.client.transact_write_items(
            TransactItems=[
                {
                    "Put": {
                        "TableName": table.name,
                        "Item": item
                        | {"tripId": trip_id}
                        | trip_gsi_keys(user_id, trip_id, item["docId"]),
                        "ConditionExpression": "attribute_not_exists(PK)",
                    }
                },
                {
                    "ConditionCheck": {
                        "TableName": table.name,
                        "Key": {"PK": f"USER#{user_id}", "SK": f"TRIP#{trip_id}"},
                        "ConditionExpression": "#s = :active",
                        "ExpressionAttributeNames": {"#s": "status"},
                        "ExpressionAttributeValues": {":active": "active"},
                    }
                },
            ]
        )
    except ClientError as err:
        if err.response["Error"]["Code"] == "TransactionCanceledException":
            return False
        raise
    return True
