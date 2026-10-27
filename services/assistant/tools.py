"""The assistant's tools: read-only, scoped to the caller, every number computed here.

The model chooses a tool and its inputs; this code decides whose data it reads (the user ID from
the sign-in token, in `Context`, never a tool input) and does all the arithmetic. Text that came
from bills or emails is redacted before it's returned to the model.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from api.documents import trips
from shared.pii import redact

MAX_BILLS = 25
MAX_TRIPS = 5
MAX_LINES = 20
MAX_QUERY = 200
MAX_ITEM_BILLS = 5  # list_bills with include_items: the newest few, with every item
MAX_PURCHASES = 25
MAX_PRICE_CHANGES = 10
ITEM_KINDS = frozenset({"item", "discount", "fee"})
# Words that say what kind of question it is, not what was bought.
_STOP = frozenset(
    "a an and at bought buy cost did do for from go how i is item items last me much my of on "
    "price prices the time to was went what when where which".split()
)
READ_STATUSES = frozenset({"ready", "needs_review"})
# Monthly equivalent of each billing cycle, as a fraction: amount × n / d.
PER_MONTH = {"weekly": (52, 12), "monthly": (1, 1), "quarterly": (1, 3), "yearly": (1, 12)}
PER_YEAR = {"weekly": 52, "monthly": 12, "quarterly": 4, "yearly": 1}


@dataclass(frozen=True)
class Context:
    table: Any
    user_id: str  # from the JWT, never from the model
    today: date
    # Semantic search (RAG): a query → matching items as (docId, row), best first.
    search: Callable[[str], list[tuple[str, int]]] | None = None


class ToolError(ValueError):
    """A bad tool call; the message goes back to the model so it can correct itself."""


SPECS: list[dict[str, Any]] = [
    {
        "name": "list_bills",
        "description": (
            "The user's read receipts and bills, newest first, with the total of all matches and "
            "the biggest one. Use for spending questions ('how much at Trader Joe's in "
            "September?', 'my biggest purchase in October?')."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "from": {"type": "string", "description": "First day, YYYY-MM-DD."},
                    "to": {"type": "string", "description": "Last day, YYYY-MM-DD."},
                    "vendor": {"type": "string", "description": "Part of the store's name."},
                    "include_items": {
                        "type": "boolean",
                        "description": "Also return every item and price of the newest 5 bills "
                        "(e.g. 'what did I buy at Costco last time?').",
                    },
                },
            }
        },
    },
    {
        "name": "get_subscriptions",
        "description": (
            "Active subscriptions and recurring bills: amount, cycle, next renewal, any price "
            "change, and monthly and yearly totals."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "renewing_within_days": {
                        "type": "integer",
                        "description": "Only those renewing in the next N days.",
                    }
                },
            }
        },
    },
    {
        "name": "get_splits",
        "description": (
            "Bills split with friends (not trips): each person's share, whether they've paid you "
            "back, and what each person still owes you in total."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "unpaid_only": {"type": "boolean"},
                    "from": {"type": "string", "description": "YYYY-MM-DD"},
                    "to": {"type": "string", "description": "YYYY-MM-DD"},
                },
            }
        },
    },
    {
        "name": "get_trips",
        "description": (
            "Trips: total spent, each person's share, and the settle-up payments (who pays whom)."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Part of the trip name."}},
            }
        },
    },
    {
        "name": "search_items",
        "description": (
            "Find every purchase of an item across all bills, newest first, with date, store, "
            "and price. Matches words ('egg' finds 'ORGANIC EGGS 12CT') and meaning ('usb "
            "cable' finds 'USB C CBL 2M'). Use for 'what did X cost', 'when did I last buy', "
            "'where did I buy', and price-history questions."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "What to look for."}},
                "required": ["query"],
            }
        },
    },
]


def run(ctx: Context, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Run one tool. Errors come back as {"error": ...}, for the model to read and correct."""
    handler = _TOOLS.get(name)
    if handler is None:
        return {"error": f"Unknown tool {name!r}."}
    try:
        return _redacted(handler(ctx, args if isinstance(args, dict) else {}))
    except ToolError as err:
        return {"error": str(err)}


# --- tools ---------------------------------------------------------------------------------------


def list_bills(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    start, end = _date(args.get("from")), _date(args.get("to"))
    vendor = _text(args.get("vendor"))
    matches = []
    for doc in _documents(ctx):
        day = _day(doc)
        if (start and day < start) or (end and day > end):
            continue
        if vendor and vendor.casefold() not in (doc.get("vendor") or "").casefold():
            continue
        if doc.get("kind") == "owed":  # money lent isn't spending (get_splits has it)
            continue
        matches.append(doc)
    matches.sort(key=lambda d: (_day(d), d["docId"]), reverse=True)
    bills = [_bill(d) for d in matches[:MAX_BILLS]]
    if args.get("include_items") is True:
        for entry in bills[:MAX_ITEM_BILLS]:
            entry["items"] = _items(_full(ctx, entry["docId"]) or {})
    result: dict[str, Any] = {"count": len(matches), "bills": bills, **_totals(matches)}
    priced = [d for d in matches if d.get("totalCents") is not None]
    if priced and "totalCents" in result:  # one currency: totals can be compared
        result["biggest"] = _bill(max(priced, key=lambda d: int(d["totalCents"])))
    if len(matches) > MAX_BILLS:
        result["note"] = f"Showing the newest {MAX_BILLS}; the total covers all {len(matches)}."
    return result


def get_subscriptions(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    within = args.get("renewing_within_days")
    if within is not None and (not isinstance(within, int) or not 0 <= within <= 366):
        raise ToolError("renewing_within_days must be a whole number of days (0–366).")
    items = _query(ctx, "SUB#")
    active = [s for s in items if s.get("status") != "cancelled"]
    if within is not None:
        last = ctx.today + timedelta(days=within)
        active = [
            s
            for s in active
            if s.get("nextRenewal")
            and ctx.today.isoformat() <= s["nextRenewal"] <= last.isoformat()
        ]
    active.sort(key=lambda s: (s.get("nextRenewal") or "9999", s.get("service", "")))
    subs = []
    monthly = yearly = 0
    for s in active:
        cents, cycle = _int(s.get("amountCents")), s.get("cycle")
        entry: dict[str, Any] = {
            "service": s.get("service"),
            "amountCents": cents,
            "currency": s.get("currency"),
            "cycle": cycle,
            "nextRenewal": s.get("nextRenewal"),
        }
        if s.get("previousAmountCents") is not None:
            entry["priceChange"] = {
                "fromCents": _int(s["previousAmountCents"]),
                "toCents": cents,
                "on": s.get("priceChangedAt"),
            }
        if cents is not None and cycle in PER_MONTH:
            num, den = PER_MONTH[cycle]
            monthly += (cents * num * 2 + den) // (2 * den)  # rounded half up, in whole cents
            yearly += cents * PER_YEAR[cycle]
        subs.append(entry)
    result: dict[str, Any] = {
        "subscriptions": subs,
        "monthlyTotalCents": monthly,
        "yearlyTotalCents": yearly,
    }
    if within is not None:
        result["renewingTotalCents"] = sum(_int(s.get("amountCents")) or 0 for s in active)
    if any(s.get("cycle") not in PER_MONTH for s in active):
        result["note"] = "Subscriptions with an unknown cycle aren't in the monthly/yearly totals."
    return result


def get_splits(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    start, end = _date(args.get("from")), _date(args.get("to"))
    unpaid_only = args.get("unpaid_only") is True
    owed: dict[str, int] = {}
    bills = []
    for doc in _documents(ctx):
        split = doc.get("split")
        if not split or doc.get("tripId"):  # trips settle up as a whole: see get_trips
            continue
        day = _day(doc)
        if (start and day < start) or (end and day > end):
            continue
        names = {m["id"]: m["name"] for m in split.get("members", [])}
        you = {m["id"] for m in split.get("members", []) if m.get("self")}
        paid = split.get("paid") or {}
        people = []
        unpaid = False
        for share in split.get("shares", []):
            member, cents = share["member"], _int(share["totalCents"]) or 0
            person = {"name": names.get(member), "shareCents": cents}
            if member in you:
                person["isYou"] = True
            else:
                person["paidYou"] = member in paid
                if member not in paid:
                    unpaid = True
                    owed[names[member]] = owed.get(names[member], 0) + cents
            people.append(person)
        if unpaid or not unpaid_only:
            bills.append({**_bill(doc), "people": people})
    bills.sort(key=lambda b: (b["date"], b["docId"]), reverse=True)
    return {"bills": bills[:MAX_BILLS], "stillOwedCents": owed}


def get_trips(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    name = _text(args.get("name"))
    listed = trips.list_trips(ctx.table, ctx.user_id)
    found = [t for t in [listed["active"], *listed["past"]] if t]
    if name:
        found = [t for t in found if name.casefold() in t["name"].casefold()]
    out = []
    for summary in found[:MAX_TRIPS]:
        trip = trips.get_trip(ctx.table, ctx.user_id, summary["tripId"])
        out.append(
            {
                "tripId": trip["tripId"],
                "name": trip["name"],
                "status": trip["status"],
                "startedAt": trip.get("startedAt"),
                "endedAt": trip.get("endedAt"),
                "totalCents": trip["totalCents"],
                "receiptCount": len(trip["receipts"]),
                "notSplitYet": len(trip["unsplit"]),
                "eachPersonsShareCents": trip["spent"],
                "payments": [
                    {
                        "from": p["from"],
                        "to": p["to"],
                        "amountCents": p["amountCents"],
                        "settled": bool(p["settledAt"]),
                    }
                    for p in trip["payments"]
                ],
            }
        )
    return {"trips": out}


def search_items(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """Every purchase matching the query, newest first: word matches over all bills, plus the
    items the meaning search (RAG) finds that no word matched, marked `"match": "meaning"`."""
    query = _text(args.get("query"))
    if not query:
        raise ToolError("query is required.")
    words = _words(query) - _STOP
    docs = [
        d
        for d in _query(ctx, "DOC#", [*DOC_FIELDS, "lines"])
        if d.get("status") in READ_STATUSES and d.get("kind") != "owed"  # money lent isn't bought
    ]
    purchases = []
    matched = set()
    for doc in docs:
        for line in _item_lines(doc):
            if words and _matches(words, line["description"]):
                purchases.append(_purchase(doc, line))
                matched.add((doc["docId"], int(line["row"])))
    if ctx.search is not None:
        by_id = {d["docId"]: d for d in docs}
        for doc_id, row in ctx.search(query[:MAX_QUERY]):
            doc = by_id.get(doc_id)  # the caller's own, read bills only
            line = next((ln for ln in _item_lines(doc or {}) if int(ln["row"]) == row), None)
            if doc and line and (doc_id, row) not in matched:
                purchases.append(_purchase(doc, line) | {"match": "meaning"})
                matched.add((doc_id, row))
    purchases.sort(key=lambda p: (p["date"], p["docId"]), reverse=True)
    result: dict[str, Any] = {"purchases": purchases[:MAX_PURCHASES]}
    changes = _price_changes(purchases)
    if changes:
        result["priceChanges"] = changes
    if len(purchases) > MAX_PURCHASES:
        result["note"] = f"Showing the newest {MAX_PURCHASES} of {len(purchases)}."
    return result


_TOOLS: dict[str, Callable[[Context, dict[str, Any]], dict[str, Any]]] = {
    "list_bills": list_bills,
    "get_subscriptions": get_subscriptions,
    "get_splits": get_splits,
    "get_trips": get_trips,
    "search_items": search_items,
}


# --- helpers -------------------------------------------------------------------------------------


# What list_bills and get_splits need: not the lines, which are most of a bill's size.
DOC_FIELDS = [
    "docId",
    "status",
    "vendor",
    "documentDate",
    "createdAt",
    "totalCents",
    "currency",
    "split",
    "tripId",
    "kind",
]


def _query(ctx: Context, prefix: str, fields: list[str] | None = None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    query: dict[str, Any] = {
        "KeyConditionExpression": "PK = :pk AND begins_with(SK, :p)",
        "ExpressionAttributeValues": {":pk": f"USER#{ctx.user_id}", ":p": prefix},
    }
    if fields:
        query["ProjectionExpression"] = ", ".join(f"#f{i}" for i in range(len(fields)))
        query["ExpressionAttributeNames"] = {f"#f{i}": f for i, f in enumerate(fields)}
    while True:
        page = ctx.table.query(**query)
        items.extend(page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            return items
        query["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def _documents(ctx: Context) -> list[dict[str, Any]]:
    return [d for d in _query(ctx, "DOC#", DOC_FIELDS) if d.get("status") in READ_STATUSES]


def _full(ctx: Context, doc_id: str) -> dict[str, Any] | None:
    return ctx.table.get_item(Key={"PK": f"USER#{ctx.user_id}", "SK": f"DOC#{doc_id}"}).get("Item")


def _items(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """What was bought (items, discounts, fees), in receipt order, capped."""
    return [
        {"description": line["description"], "amountCents": _int(line.get("amountCents"))}
        for line in doc.get("lines") or []
        if line.get("kind") in ITEM_KINDS and line.get("description")
    ][:MAX_LINES]


def _item_lines(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """A bill's item, discount, and fee lines as stored (with their row numbers)."""
    return [
        line
        for line in doc.get("lines") or []
        if line.get("kind") in ITEM_KINDS and line.get("description") and "row" in line
    ]


def _purchase(doc: dict[str, Any], line: dict[str, Any]) -> dict[str, Any]:
    return {
        "docId": doc["docId"],
        "date": _day(doc),
        "vendor": doc.get("vendor"),
        "description": line["description"],
        "amountCents": _int(line.get("amountCents")),
        "currency": doc.get("currency"),
    }


def _price_changes(purchases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """For each item bought more than once (same description): first and latest price, and the
    change in cents, computed here so the model never does the arithmetic."""
    by_item: dict[str, list[dict[str, Any]]] = {}
    for p in purchases:
        if p["amountCents"] is not None:
            by_item.setdefault(p["description"].casefold(), []).append(p)
    changes = []
    for bought in by_item.values():
        if len(bought) < 2:
            continue
        bought = sorted(bought, key=lambda p: p["date"])
        first, latest = bought[0], bought[-1]
        changes.append(
            {
                "description": latest["description"],
                "firstDate": first["date"],
                "firstCents": first["amountCents"],
                "latestDate": latest["date"],
                "latestCents": latest["amountCents"],
                "changeCents": latest["amountCents"] - first["amountCents"],
            }
        )
    changes.sort(key=lambda c: c["latestDate"], reverse=True)
    return changes[:MAX_PRICE_CHANGES]


def _words(text: str) -> set[str]:
    """Lowercase words, plurals trimmed: "EGGS 12CT" → {"egg", "12ct"}."""
    out = set()
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        out.add(word[:-1] if len(word) > 3 and word.endswith("s") else word)
    return out


def _matches(query_words: set[str], description: str) -> bool:
    """A line matches when one of its words is, or starts with, a query word ("egg" → "eggs")."""
    line_words = _words(description)
    return any(w == q or (len(q) >= 3 and w.startswith(q)) for q in query_words for w in line_words)


def _bill(doc: dict[str, Any]) -> dict[str, Any]:
    out = {
        "docId": doc["docId"],
        "date": _day(doc),
        "vendor": doc.get("vendor"),
        "totalCents": _int(doc.get("totalCents")),
        "currency": doc.get("currency"),
    }
    if doc.get("tripId"):
        out["inTrip"] = True
    return out


def _totals(docs: list[dict[str, Any]]) -> dict[str, Any]:
    by_currency: dict[str, int] = {}
    for d in docs:
        if d.get("totalCents") is not None:
            currency = d.get("currency") or "USD"
            by_currency[currency] = by_currency.get(currency, 0) + int(d["totalCents"])
    if len(by_currency) <= 1:
        return {"totalCents": sum(by_currency.values()), "currency": next(iter(by_currency), "USD")}
    return {"totalsByCurrency": by_currency}


def _day(doc: dict[str, Any]) -> str:
    return doc.get("documentDate") or (doc.get("createdAt") or "")[:10]


def _date(value: Any) -> str | None:
    if value is None or value == "":
        return None
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as err:
        raise ToolError(f"Dates must be YYYY-MM-DD, not {value!r}.") from err


def _text(value: Any) -> str:
    return " ".join(value.split())[:MAX_QUERY] if isinstance(value, str) else ""


def _int(value: Any) -> int | None:
    return None if value is None else int(value)


def _redacted(value: Any, key: str = "") -> Any:
    """Mask emails, card and account numbers in text that came from bills (ids stay as they are)."""
    if isinstance(value, Decimal):
        return int(value)
    if isinstance(value, str):
        return value if key in {"docId", "tripId", "date"} else redact(value).text
    if isinstance(value, dict):
        # Keys too: some results are keyed by names people typed.
        return {
            (redact(k).text if isinstance(k, str) else k): _redacted(v, k) for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redacted(v, key) for v in value]
    return value
