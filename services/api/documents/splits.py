"""Save a bill split: validate it against the document's stored lines, compute the shares in
code (split.allocate), and store them on the DOC item. (Recent friends live only on the device.)

The client sends people by seat index and lines by row number; it never sends amounts. Every
amount comes from the stored, reconciled lines, so a split can't change what the bill says.
"""

import re
from datetime import datetime
from typing import Any

from botocore.exceptions import ClientError

from api.documents.logic import (
    MAX_LINES,
    REVIEWABLE_STATUSES,
    BadRequest,
    Conflict,
    get_document,
)
from split.allocate import (
    EVEN,
    MAX_MEMBERS,
    PROPORTIONAL,
    ChargeMode,
    SplitError,
    SplitLine,
    allocate,
    one_person,
    split_target,
)
from split.names import MAX_NAME

MIN_MEMBERS = 2
_ROW = re.compile(r"^\d{1,3}$")


def save_split(
    table: Any, user_id: str, doc_id: str, body: dict[str, Any], now: datetime
) -> dict[str, Any]:
    doc = get_document(table, user_id, doc_id, now)  # NotFound for other users' documents
    if doc.get("status") not in REVIEWABLE_STATUSES:
        raise BadRequest("Only processed documents can be split.")
    seen = body.get("expectedUpdatedAt")
    if not isinstance(seen, str):
        raise BadRequest("expectedUpdatedAt (the document version you're splitting) is required.")
    if seen != doc.get("updatedAt"):
        # Lines may have been edited (and rows renumbered) since this split was made.
        raise Conflict("This bill changed since you opened it. Reload and try again.")
    members = _members(body.get("members"))
    ids = [m["id"] for m in members]
    assignments = _assignments(body.get("assignments"), ids)
    charges = _charges(body.get("charges"), ids)
    expected = body.get("expectedVersion", 0)
    if not isinstance(expected, int) or isinstance(expected, bool) or expected < 0:
        raise BadRequest("expectedVersion must be a whole number.")
    current = int((doc.get("split") or {}).get("version", 0))
    if expected != current:
        raise Conflict("This split was changed somewhere else. Reload and try again.")

    lines = [
        SplitLine(int(line["row"]), line["kind"], int(line["amountCents"]))
        for line in doc.get("lines") or []
    ]
    if doc.get("totalCents") is None:
        raise BadRequest("This document has no total to split.")
    tax_included = bool(doc.get("taxIncluded"))
    try:
        shares = allocate(ids, lines, assignments, charges, int(doc["totalCents"]), tax_included)
    except SplitError as err:
        raise BadRequest(str(err)) from err
    target, matches = split_target(lines, int(doc["totalCents"]), tax_included)

    split = {
        "version": current + 1,
        "members": members,
        "assignments": {str(row): people for row, people in sorted(assignments.items())},
        "charges": {
            str(row): {"mode": mode.mode} | ({"member": mode.member} if mode.member else {})
            for row, mode in sorted(charges.items())
        },
        "shares": [
            {
                "member": s.member,
                "totalCents": s.total_cents,
                "itemsCents": s.items_cents,
                "extrasCents": s.extras_cents,
                "rows": s.rows,
            }
            for s in shares
        ],
        "totalCents": target,
        # When a misread total doesn't match the lines, the split uses the recorded lines.
        "receiptTotalCents": int(doc["totalCents"]),
        "matchesReceipt": matches,
        "taxIncluded": tax_included,
        "updatedAt": now.isoformat(),
    }
    _store(table, user_id, doc_id, split, expected, seen)
    return split


def _members(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not MIN_MEMBERS <= len(raw) <= MAX_MEMBERS:
        raise BadRequest(f"A split needs {MIN_MEMBERS}–{MAX_MEMBERS} people.")
    members, seen = [], set()
    for seat, entry in enumerate(raw):
        name = entry.get("name") if isinstance(entry, dict) else None
        if not isinstance(name, str) or not 0 < len(" ".join(name.split())) <= MAX_NAME:
            raise BadRequest(f"Each person needs a name of 1–{MAX_NAME} characters.")
        name = " ".join(name.split())
        if name.casefold() in seen:
            raise BadRequest(f"{name} is in the split twice.")
        seen.add(name.casefold())
        is_self = entry.get("self") is True
        members.append(
            {"id": f"m{seat}", "name": name, "self": is_self, "color": seat % MAX_MEMBERS}
        )
    if sum(m["self"] for m in members) != 1:
        raise BadRequest("Exactly one person in the split must be you.")
    return members


def _seat(value: Any, ids: list[str]) -> str:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < len(ids):
        raise BadRequest("A line is assigned to someone who isn't in the split.")
    return ids[value]


def _row(key: Any) -> int:
    if not isinstance(key, str) or not _ROW.fullmatch(key):
        raise BadRequest("Lines are identified by their row number.")
    return int(key)


def _assignments(raw: Any, ids: list[str]) -> dict[int, list[str]]:
    if not isinstance(raw, dict) or len(raw) > MAX_LINES:
        raise BadRequest("assignments must map row numbers to people.")
    out = {}
    for key, seats in raw.items():
        if not isinstance(seats, list) or len(seats) > MAX_MEMBERS:
            raise BadRequest("Each line needs a list of people.")
        out[_row(key)] = [_seat(seat, ids) for seat in seats]
    return out


def _charges(raw: Any, ids: list[str]) -> dict[int, ChargeMode]:
    if not isinstance(raw, dict) or len(raw) > MAX_LINES:
        raise BadRequest("charges must map row numbers to a choice.")
    out: dict[int, ChargeMode] = {}
    for key, choice in raw.items():
        mode = choice.get("mode") if isinstance(choice, dict) else None
        if mode == "proportional":
            out[_row(key)] = PROPORTIONAL
        elif mode == "even":
            out[_row(key)] = EVEN
        elif mode == "person":
            out[_row(key)] = one_person(_seat(choice.get("member"), ids))
        else:
            raise BadRequest("Choose proportional, even, or one person for each charge.")
    return out


def _store(
    table: Any,
    user_id: str,
    doc_id: str,
    split: dict[str, Any],
    expected: int,
    seen_updated_at: str | None,
) -> None:
    """Write only if the split version and the document are unchanged since they were read."""
    condition = "#status IN (:ready, :review) AND #updatedAt = :seen AND "
    condition += "attribute_not_exists(#split)" if expected == 0 else "#split.#version = :expected"
    values = {
        ":split": split,
        ":now": split["updatedAt"],
        ":ready": "ready",
        ":review": "needs_review",
        ":seen": seen_updated_at,
    }
    if expected:
        values[":expected"] = expected
    try:
        table.update_item(
            Key={"PK": f"USER#{user_id}", "SK": f"DOC#{doc_id}"},
            UpdateExpression="SET #split = :split, #updatedAt = :now",
            ConditionExpression=condition,
            ExpressionAttributeNames={
                "#split": "split",
                "#status": "status",
                "#updatedAt": "updatedAt",
            }
            | ({"#version": "version"} if expected else {}),
            ExpressionAttributeValues=values,
        )
    except ClientError as err:
        if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise Conflict(
                "This bill changed while you were splitting it. Reload and try again."
            ) from err
        raise
