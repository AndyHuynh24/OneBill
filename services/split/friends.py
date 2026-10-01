"""Recent friends, for one-tap chips on "Who's here?".

Stored under the caller's own partition as `USER#<id> / FRIEND#<friendId>`. The ID is derived
from the name (case-insensitive), so remembering the same person again updates one item and
needs no read first. IDs are hashes: names never appear in URLs or API access logs.
"""

import hashlib
import re
from datetime import datetime
from typing import Any

COLORS = 12
MAX_LISTED = 50
_ID = re.compile(r"^[0-9a-f]{16}$")


class InvalidFriend(ValueError):
    pass


def friend_id(name: str) -> str:
    return hashlib.sha256(" ".join(name.split()).casefold().encode()).hexdigest()[:16]


def remember_friends(table: Any, user_id: str, names: list[str], now: datetime) -> None:
    """Count a use for each name; the latest spelling wins and a friend keeps their color."""
    for name in names:
        fid = friend_id(name)
        table.update_item(
            Key={"PK": f"USER#{user_id}", "SK": f"FRIEND#{fid}"},
            UpdateExpression=(
                "SET #type = :type, friendId = :fid, #name = :name, lastUsedAt = :now, "
                "color = if_not_exists(color, :color) ADD timesUsed :one"
            ),
            ExpressionAttributeNames={"#type": "type", "#name": "name"},
            ExpressionAttributeValues={
                ":type": "FRIEND",
                ":fid": fid,
                ":name": name,
                ":now": now.isoformat(),
                ":color": int(fid, 16) % COLORS,
                ":one": 1,
            },
        )


def list_friends(table: Any, user_id: str) -> list[dict[str, Any]]:
    """Most-used first, then most recent; at most MAX_LISTED."""
    response = table.query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
        ExpressionAttributeValues={":pk": f"USER#{user_id}", ":prefix": "FRIEND#"},
    )
    friends = [
        {
            "friendId": item["friendId"],
            "name": item["name"],
            "color": int(item["color"]),
            "timesUsed": int(item["timesUsed"]),
            "lastUsedAt": item["lastUsedAt"],
        }
        for item in response.get("Items", [])
    ]
    friends.sort(key=lambda f: (f["timesUsed"], f["lastUsedAt"]), reverse=True)
    return friends[:MAX_LISTED]


def delete_friend(table: Any, user_id: str, fid: str) -> None:
    """Remove a chip. Deleting one that's already gone is fine."""
    if not _ID.fullmatch(fid or ""):
        raise InvalidFriend("Unknown friend.")
    table.delete_item(Key={"PK": f"USER#{user_id}", "SK": f"FRIEND#{fid}"})
