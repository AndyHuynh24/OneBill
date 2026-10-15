"""Trip keys shared by uploads and trips (kept apart from trips.py to avoid an import cycle)."""

from typing import Any

from botocore.exceptions import ClientError

ACTIVE_SK = "ACTIVE_TRIP"


def trip_gsi_keys(user_id: str, trip_id: str, doc_id: str) -> dict[str, str]:
    """Sparse GSI2 keys for a receipt in a trip; the user ID in the key scopes the query."""
    return {"GSI2PK": f"USER#{user_id}#TRIP#{trip_id}", "GSI2SK": f"DOC#{doc_id}"}


def active_trip_id(table: Any, user_id: str) -> str | None:
    item = table.get_item(Key={"PK": f"USER#{user_id}", "SK": ACTIVE_SK}).get("Item")
    return item.get("tripId") if item else None


def put_in_trip(table: Any, item: dict[str, Any], user_id: str, trip_id: str) -> bool:
    """Put a new DOC in a trip only if that trip is still the active one (one transaction, so a
    trip ended mid-upload can't gain a receipt). False: the trip ended; nothing was written."""
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
                        "Key": {"PK": f"USER#{user_id}", "SK": ACTIVE_SK},
                        "ConditionExpression": "tripId = :t",
                        "ExpressionAttributeValues": {":t": trip_id},
                    }
                },
            ]
        )
    except ClientError as err:
        if err.response["Error"]["Code"] == "TransactionCanceledException":
            return False
        raise
    return True
