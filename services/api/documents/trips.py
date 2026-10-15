"""Trips: a named group of people whose receipts settle up together.

Items:
- `USER#<id> / TRIP#<tripId>`: name, members, status (active | ended), settled ticks.
- `USER#<id> / ACTIVE_TRIP`: points at the one active trip. Starting a trip creates it only if
  it doesn't exist (one active trip, even with two taps at once), and uploads read it to join.
- A receipt in a trip has `tripId` and sparse GSI2 keys (`USER#<id>#TRIP#<tripId>`,
  `DOC#<docId>`), so one query lists a trip's receipts, scoped to the caller by the key.

Balances and payments are computed here from the stored splits (split/settle.py), never stored:
a tick is kept as {from, to, amount} and only counts while that exact payment still exists.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from botocore.exceptions import ClientError

from api.documents.logic import BadRequest, Conflict, NotFound, get_document
from api.documents.trip_keys import ACTIVE_SK, trip_gsi_keys
from shared.ids import is_valid_id, new_id
from split.allocate import MAX_MEMBERS
from split.names import MAX_NAME
from split.settle import Receipt, balances, settle

MAX_TRIP_NAME = 60
MAX_LISTED = 20
MAX_TRIP_RECEIPTS = 100


def _pk(user_id: str) -> str:
    return f"USER#{user_id}"


# --- trips -------------------------------------------------------------------------------------


def create_trip(table: Any, user_id: str, body: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    name = _name(body.get("name"))
    members = _members(body.get("members"))
    trip_id = new_id(int(now.timestamp() * 1000))
    trip = {
        "tripId": trip_id,
        "name": name,
        "members": members,
        "status": "active",
        "startedAt": now.isoformat(),
        "updatedAt": now.isoformat(),
        "settled": {},
    }
    try:
        table.meta.client.transact_write_items(
            TransactItems=[
                {
                    "Put": {
                        "TableName": table.name,
                        "Item": {"PK": _pk(user_id), "SK": ACTIVE_SK, "tripId": trip_id},
                        "ConditionExpression": "attribute_not_exists(PK)",
                    }
                },
                {
                    "Put": {
                        "TableName": table.name,
                        "Item": {"PK": _pk(user_id), "SK": f"TRIP#{trip_id}", "type": "TRIP"}
                        | trip,
                        "ConditionExpression": "attribute_not_exists(PK)",
                    }
                },
            ]
        )
    except ClientError as err:
        if err.response["Error"]["Code"] == "TransactionCanceledException":
            raise Conflict("A trip is already on. End it before starting another.") from err
        raise
    return trip


def list_trips(table: Any, user_id: str) -> dict[str, Any]:
    """The active trip (with its receipt count and total) and the most recent past trips (no
    totals: those come with the trip itself, so listing stays one query plus one)."""
    items = table.query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
        ExpressionAttributeValues={":pk": _pk(user_id), ":prefix": "TRIP#"},
        ScanIndexForward=False,  # tripIds are time-ordered: newest first
        Limit=MAX_LISTED,
    )["Items"]
    active, past = None, []
    for item in items:
        if item["status"] == "active":
            receipts = _receipts(table, user_id, item["tripId"])
            active = _trip_view(item) | {
                "receiptCount": len(receipts),
                "totalCents": sum(int(r.get("totalCents") or 0) for r in receipts),
            }
        else:
            past.append(_trip_view(item))
    return {"active": active, "past": past}


def get_trip(table: Any, user_id: str, trip_id: str) -> dict[str, Any]:
    trip = _get(table, user_id, trip_id)
    receipts = _receipts(table, user_id, trip_id)
    self_name = next((m["name"] for m in trip["members"] if m.get("self")), "Me")

    rows, settled_receipts, unsplit = [], [], []
    spent: dict[str, int] = {}
    for doc in receipts:
        split = doc.get("split")
        paid_by = None
        if split:
            names = {m["id"]: (self_name if m.get("self") else m["name"]) for m in split["members"]}
            shares = {names[s["member"]]: int(s["totalCents"]) for s in split["shares"]}
            paid_by = names.get(split.get("paidBy")) or self_name
            settled_receipts.append(Receipt(paid_by=paid_by, shares=shares))
            for person, cents in shares.items():
                spent[person] = spent.get(person, 0) + cents
        elif doc.get("status") in {"ready", "needs_review"}:
            unsplit.append(doc["docId"])
        rows.append(
            {
                "docId": doc["docId"],
                "vendor": doc.get("vendor"),
                "documentDate": doc.get("documentDate"),
                "totalCents": _int(doc.get("totalCents")),
                "currency": doc.get("currency"),
                "status": doc.get("status"),
                "split": bool(split),
                "paidBy": paid_by,
            }
        )

    ticks = trip.get("settled") or {}
    payments = []
    for p in settle(balances(settled_receipts)):
        tick = ticks.get(_tick_key(p.from_name, p.to_name))
        current = tick is not None and int(tick["amountCents"]) == p.amount_cents
        payments.append(
            {
                "from": p.from_name,
                "to": p.to_name,
                "amountCents": p.amount_cents,
                "settledAt": tick["at"] if current else None,
            }
        )
    live = {_tick_key(p["from"], p["to"]) for p in payments if p["settledAt"]}
    return _trip_view(trip) | {
        "receipts": rows,
        "totalCents": sum(r["totalCents"] or 0 for r in rows),
        "spent": spent,
        "payments": payments,
        "unsplit": unsplit,
        # A tick whose payment changed (a new receipt, an edit): the screen says to check again.
        "balancesChanged": any(key not in live for key in ticks),
    }


def update_trip(
    table: Any, user_id: str, trip_id: str, body: Mapping[str, Any], now: datetime
) -> dict[str, Any]:
    trip = _get(table, user_id, trip_id)
    updates: dict[str, Any] = {"updatedAt": now.isoformat()}
    if "name" in body:
        updates["name"] = _name(body["name"])
    if "members" in body:
        updates["members"] = _members(body["members"])
    ending = body.get("end") is True
    if ending:
        if trip["status"] != "active":
            raise BadRequest("This trip has already ended.")
        updates |= {"status": "ended", "endedAt": now.isoformat()}
    if len(updates) == 1:
        raise BadRequest("Nothing to change.")

    table.update_item(
        Key={"PK": _pk(user_id), "SK": f"TRIP#{trip_id}"},
        UpdateExpression="SET " + ", ".join(f"#f{i} = :v{i}" for i in range(len(updates))),
        ConditionExpression="attribute_exists(PK)",
        ExpressionAttributeNames={f"#f{i}": k for i, k in enumerate(updates)},
        ExpressionAttributeValues={f":v{i}": v for i, v in enumerate(updates.values())},
    )
    if ending:
        _clear_active_pointer(table, user_id, trip_id)
    return _trip_view(trip | updates)


def delete_trip(table: Any, user_id: str, trip_id: str) -> None:
    """The trip goes; its receipts stay, no longer in any trip."""
    trip = _get(table, user_id, trip_id)
    for doc in _receipts(table, user_id, trip_id):
        _unlink(table, user_id, doc["docId"], trip_id)
    table.delete_item(Key={"PK": _pk(user_id), "SK": f"TRIP#{trip_id}"})
    if trip["status"] == "active":
        _clear_active_pointer(table, user_id, trip_id)


# --- receipts ------------------------------------------------------------------------------------


def add_document(table: Any, user_id: str, trip_id: str, doc_id: str) -> None:
    _get(table, user_id, trip_id)
    get_document(table, user_id, doc_id)  # NotFound for other users' documents
    if len(_receipts(table, user_id, trip_id)) >= MAX_TRIP_RECEIPTS:
        raise BadRequest(f"A trip can have at most {MAX_TRIP_RECEIPTS} receipts.")
    table.update_item(
        Key={"PK": _pk(user_id), "SK": f"DOC#{doc_id}"},
        UpdateExpression="SET tripId = :t, GSI2PK = :gpk, GSI2SK = :gsk",
        ConditionExpression="attribute_exists(PK)",
        ExpressionAttributeValues={
            ":t": trip_id,
            ":gpk": trip_gsi_keys(user_id, trip_id, doc_id)["GSI2PK"],
            ":gsk": f"DOC#{doc_id}",
        },
    )


def remove_document(table: Any, user_id: str, trip_id: str, doc_id: str) -> None:
    _get(table, user_id, trip_id)
    get_document(table, user_id, doc_id)
    _unlink(table, user_id, doc_id, trip_id)


# --- settle-up ticks -----------------------------------------------------------------------------


def set_settled(
    table: Any, user_id: str, trip_id: str, body: Mapping[str, Any], now: datetime
) -> dict[str, Any]:
    """Tick (or untick) one payment of the current settle-up list."""
    sender, receiver, paid = body.get("from"), body.get("to"), body.get("paid")
    if not isinstance(sender, str) or not isinstance(receiver, str) or not isinstance(paid, bool):
        raise BadRequest("from, to (names) and paid (true or false) are required.")
    view = get_trip(table, user_id, trip_id)
    wanted = _tick_key(sender, receiver)
    payment = next((p for p in view["payments"] if _tick_key(p["from"], p["to"]) == wanted), None)
    if payment is None:
        raise BadRequest("That payment is no longer in the settle-up list. Reload and check.")
    key = _tick_key(payment["from"], payment["to"])
    if paid:
        update = "SET settled.#k = :tick, updatedAt = :now"
        values = {":tick": {"amountCents": payment["amountCents"], "at": now.isoformat()}}
    else:
        update = "REMOVE settled.#k SET updatedAt = :now"
        values = {}
    table.update_item(
        Key={"PK": _pk(user_id), "SK": f"TRIP#{trip_id}"},
        UpdateExpression=update,
        ConditionExpression="attribute_exists(PK)",
        ExpressionAttributeNames={"#k": key},
        ExpressionAttributeValues=values | {":now": now.isoformat()},
    )
    return get_trip(table, user_id, trip_id)


# --- helpers -------------------------------------------------------------------------------------


def _get(table: Any, user_id: str, trip_id: str) -> dict[str, Any]:
    if not is_valid_id(trip_id):
        raise NotFound("Trip not found.")
    item = table.get_item(Key={"PK": _pk(user_id), "SK": f"TRIP#{trip_id}"}).get("Item")
    if not item:
        raise NotFound("Trip not found.")
    return item


def _receipts(table: Any, user_id: str, trip_id: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    query: dict[str, Any] = {
        "IndexName": "GSI2",
        "KeyConditionExpression": "GSI2PK = :pk",
        "ExpressionAttributeValues": {":pk": f"USER#{user_id}#TRIP#{trip_id}"},
        "ScanIndexForward": False,  # docIds are time-ordered: newest first
    }
    while True:
        page = table.query(**query)
        items.extend(page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            return items
        query["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def _unlink(table: Any, user_id: str, doc_id: str, trip_id: str) -> None:
    try:
        table.update_item(
            Key={"PK": _pk(user_id), "SK": f"DOC#{doc_id}"},
            UpdateExpression="REMOVE tripId, GSI2PK, GSI2SK",
            ConditionExpression="tripId = :t",
            ExpressionAttributeValues={":t": trip_id},
        )
    except ClientError as err:
        if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise  # not in this trip (or already removed): nothing to do


def _clear_active_pointer(table: Any, user_id: str, trip_id: str) -> None:
    """Best effort, after the trip itself changed: a missing pointer (or one already pointing
    elsewhere) must never stop a trip from ending or being deleted. Uploads re-check the trip
    in their own transaction, so a stale pointer can't add a receipt to an ended trip."""
    try:
        table.delete_item(
            Key={"PK": _pk(user_id), "SK": ACTIVE_SK},
            ConditionExpression="tripId = :t",
            ExpressionAttributeValues={":t": trip_id},
        )
    except ClientError as err:
        if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise


def _trip_view(item: Mapping[str, Any]) -> dict[str, Any]:
    keys = ("tripId", "name", "members", "status", "startedAt", "endedAt")
    return {k: item[k] for k in keys if k in item}


def _tick_key(sender: str, receiver: str) -> str:
    return f"{sender.casefold()}>{receiver.casefold()}"


def _int(value: Any) -> int | None:
    return None if value is None else int(value)


def _name(raw: Any) -> str:
    name = " ".join(raw.split()) if isinstance(raw, str) else ""
    if not 0 < len(name) <= MAX_TRIP_NAME:
        raise BadRequest(f"Give the trip a name of 1–{MAX_TRIP_NAME} characters.")
    return name


def _members(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not 2 <= len(raw) <= MAX_MEMBERS:
        raise BadRequest(f"A trip needs 2–{MAX_MEMBERS} people.")
    members, seen = [], set()
    for entry in raw:
        name = entry.get("name") if isinstance(entry, dict) else None
        name = " ".join(name.split()) if isinstance(name, str) else ""
        if not 0 < len(name) <= MAX_NAME:
            raise BadRequest(f"Each person needs a name of 1–{MAX_NAME} characters.")
        if name.casefold() in seen:
            raise BadRequest(f"{name} is in the trip twice.")
        seen.add(name.casefold())
        members.append({"name": name, "self": entry.get("self") is True})
    if sum(m["self"] for m in members) != 1:
        raise BadRequest("Exactly one person in the trip must be you.")
    return members
