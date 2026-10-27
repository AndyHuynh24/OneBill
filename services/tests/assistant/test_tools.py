"""The assistant's tools: read-only, scoped to the caller, every number computed by code."""

from datetime import UTC, date, datetime

import pytest

from api.documents import trips
from api.documents.splits import save_split
from assistant import tools

TODAY = date(2026, 10, 14)
NOW = datetime(2026, 10, 14, 18, 0, tzinfo=UTC)
ME = [{"name": "Me", "self": True}, {"name": "Minh"}, {"name": "Linh"}]


def ctx(table, user="user-a", search=None):
    return tools.Context(table=table, user_id=user, today=TODAY, search=search)


def bill(table, doc_id, vendor, day, total, user="user-a", status="ready", **extra):
    lines = [
        {"row": 1, "description": f"{vendor} ITEM", "kind": "item", "amountCents": total},
        {"row": 2, "description": "TOTAL", "kind": "total", "amountCents": total},
    ]
    table.put_item(
        Item={
            "PK": f"USER#{user}",
            "SK": f"DOC#{doc_id}",
            "docId": doc_id,
            "status": status,
            "vendor": vendor,
            "documentDate": day,
            "totalCents": total,
            "currency": "USD",
            "lines": lines,
            "updatedAt": "2026-10-01T00:00:00+00:00",
            **extra,
        }
    )


def sub(table, key, service, cents, cycle, renewal, status="charged", **extra):
    table.put_item(
        Item={
            "PK": "USER#user-a",
            "SK": f"SUB#{key}",
            "serviceKey": key,
            "service": service,
            "amountCents": cents,
            "currency": "USD",
            "cycle": cycle,
            "status": status,
            "nextRenewal": renewal,
            **extra,
        }
    )


# --- list_bills ----------------------------------------------------------------------------------


def test_list_bills_filters_by_date_and_vendor_and_sums_in_code(table):
    bill(table, "d1", "TRADER JOE'S", "2026-09-03", 4210)
    bill(table, "d2", "Trader Joe's", "2026-09-20", 1890)
    bill(table, "d3", "TARGET", "2026-09-21", 999)
    bill(table, "d4", "TRADER JOE'S", "2026-10-02", 500)  # October
    bill(table, "d5", "TRADER JOE'S", "2026-09-10", 700, status="failed")  # not read

    result = tools.run(
        ctx(table), "list_bills", {"from": "2026-09-01", "to": "2026-09-30", "vendor": "trader joe"}
    )

    assert result["count"] == 2
    assert result["totalCents"] == 6100
    assert [b["docId"] for b in result["bills"]] == ["d2", "d1"]  # newest first


def test_list_bills_names_the_biggest_bill_so_the_model_doesnt_compare(table):
    bill(table, "d1", "SKYDECK CHICAGO", "2026-10-04", 10500)
    bill(table, "d2", "HEAVENLY RENTALS", "2026-10-15", 10200)  # newest, and close
    bill(table, "d3", "STARBUCKS", "2026-10-09", 1112)

    result = tools.run(ctx(table), "list_bills", {"from": "2026-10-01"})

    assert result["biggest"]["docId"] == "d1"
    assert result["biggest"]["totalCents"] == 10500


def test_money_you_lent_isnt_spending(table):
    bill(table, "d1", "TARGET", "2026-10-03", 1000)
    bill(table, "d2", "Concert ticket", "2026-10-04", 5000, kind="owed")

    result = tools.run(ctx(table), "list_bills", {})

    assert (result["count"], result["totalCents"]) == (1, 1000)


def test_list_bills_with_nothing_found_has_no_biggest(table):
    assert "biggest" not in tools.run(ctx(table), "list_bills", {})


def test_list_bills_only_sees_your_own_bills(table):
    bill(table, "d1", "TARGET", "2026-09-03", 1000, user="user-b")

    assert tools.run(ctx(table), "list_bills", {})["count"] == 0


# --- get_subscriptions ---------------------------------------------------------------------------


def test_subscriptions_with_monthly_and_yearly_totals(table):
    sub(table, "netflix", "Netflix", 1549, "monthly", "2026-11-01")
    sub(table, "icloud", "iCloud+", 1188, "yearly", "2027-02-01")
    sub(
        table,
        "gym",
        "Gym",
        600,
        "weekly",
        "2026-10-16",
        previousAmountCents=500,
        priceChangedAt="2026-10-01",
    )
    sub(table, "hulu", "Hulu", 999, "monthly", None, status="cancelled")

    result = tools.run(ctx(table), "get_subscriptions", {})

    assert [s["service"] for s in result["subscriptions"]] == ["Gym", "Netflix", "iCloud+"]
    assert result["monthlyTotalCents"] == 1549 + 99 + 2600  # yearly/12 and weekly×52/12, rounded
    assert result["yearlyTotalCents"] == 1549 * 12 + 1188 + 600 * 52
    gym = result["subscriptions"][0]
    assert gym["priceChange"] == {"fromCents": 500, "toCents": 600, "on": "2026-10-01"}


def test_subscriptions_renewing_soon(table):
    sub(table, "gym", "Gym", 600, "weekly", "2026-10-16")
    sub(table, "netflix", "Netflix", 1549, "monthly", "2026-11-01")

    result = tools.run(ctx(table), "get_subscriptions", {"renewing_within_days": 7})

    assert [s["service"] for s in result["subscriptions"]] == ["Gym"]
    assert result["renewingTotalCents"] == 600


# --- get_splits ----------------------------------------------------------------------------------


def split(table, doc_id, people=ME, paid_by=0):
    doc = table.get_item(Key={"PK": "USER#user-a", "SK": f"DOC#{doc_id}"})["Item"]
    body = {
        "members": people,
        "assignments": {"1": list(range(len(people)))},
        "charges": {},
        "expectedVersion": 0,
        "expectedUpdatedAt": doc["updatedAt"],
        "paidBy": paid_by,
    }
    return save_split(table, "user-a", doc_id, body, NOW)


D1, D2 = "1790000000001aaaaaaaa", "1790000000002aaaaaaaa"  # real-format ids (splits check them)


def test_who_still_owes_you_across_split_bills(table):
    bill(table, D1, "THAI BASIL", "2026-10-03", 9000)
    bill(table, D2, "PIZZA", "2026-10-05", 3000)
    split(table, D1)
    paid = split(table, D2)
    linh = next(m["id"] for m in paid["members"] if m["name"] == "Linh")
    table.update_item(
        Key={"PK": "USER#user-a", "SK": f"DOC#{D2}"},
        UpdateExpression="SET #s.#p = :p",
        ExpressionAttributeNames={"#s": "split", "#p": "paid"},
        ExpressionAttributeValues={":p": {linh: "2026-10-06"}},
    )

    result = tools.run(ctx(table), "get_splits", {"unpaid_only": True})

    assert result["stillOwedCents"] == {"Minh": 3000 + 1000, "Linh": 3000}
    assert [b["vendor"] for b in result["bills"]] == ["PIZZA", "THAI BASIL"]


def test_trip_receipts_are_left_to_the_trip(table):
    bill(table, D1, "GAS", "2026-10-03", 6000, tripId="t1")
    split(table, D1)

    assert tools.run(ctx(table), "get_splits", {})["bills"] == []


# --- get_trips -----------------------------------------------------------------------------------


def test_trips_with_their_settle_up(table):
    trip = trips.create_trip(table, "user-a", {"name": "Chicago", "members": ME}, NOW)

    result = tools.run(ctx(table), "get_trips", {"name": "chi"})

    (found,) = result["trips"]
    assert (found["name"], found["status"], found["payments"]) == ("Chicago", "active", [])
    assert found["tripId"] == trip["tripId"]


# --- search_items (RAG) --------------------------------------------------------------------------


def test_search_only_returns_your_own_read_bills(table):
    bill(table, "d1", "BEST BUY", "2026-08-02", 1999)
    bill(table, "d2", "TARGET", "2026-09-02", 500, user="user-b")  # someone else's
    seen = []

    def search(query):
        seen.append(query)
        return [("d1", 1), ("d2", 1), ("missing", 1)]  # deleted or foreign: never returned

    result = tools.run(ctx(table, search=search), "search_items", {"query": "something"})

    assert seen == ["something"]
    assert {p["docId"] for p in result["purchases"]} == {"d1"}


# --- the tool runner -----------------------------------------------------------------------------


def test_a_tool_never_takes_a_user_id_from_the_model(table):
    bill(table, "d1", "TARGET", "2026-09-03", 1000, user="user-b")

    result = tools.run(ctx(table), "list_bills", {"userId": "user-b", "user_id": "user-b"})

    assert result["count"] == 0  # extra keys are ignored; the caller's id comes from the token


@pytest.mark.parametrize(
    ("name", "args"),
    [("drop_table", {}), ("list_bills", {"from": "yesterday"}), ("search_items", {"query": ""})],
)
def test_bad_tool_calls_return_an_error_the_model_can_read(table, name, args):
    assert "error" in tools.run(ctx(table, search=lambda q: []), name, args)


def test_text_from_bills_is_redacted_before_it_reaches_the_model(table):
    bill(table, "d1", "Card 4111 1111 1111 1111 · jane@example.com", "2026-09-03", 1000)

    text = str(tools.run(ctx(table), "list_bills", {}))

    assert "jane@example.com" not in text
    assert "4111 1111 1111 1111" not in text


def test_names_used_as_keys_are_redacted_too(table):
    bill(table, D1, "DINNER", "2026-10-03", 9000)
    people = [{"name": "Me", "self": True}, {"name": "bao@example.com"}]
    split(table, D1, people=people)

    text = str(tools.run(ctx(table), "get_splits", {}))

    assert "bao@example.com" not in text


# --- "what did I buy" and "what did it cost" -----------------------------------------------------


def receipt_with(table, doc_id, vendor, day, items):
    lines = [
        {"row": i + 1, "description": d, "kind": "item", "amountCents": c}
        for i, (d, c) in enumerate(items)
    ]
    total = sum(c for _, c in items)
    lines.append(
        {"row": len(lines) + 1, "description": "TOTAL", "kind": "total", "amountCents": total}
    )
    bill(table, doc_id, vendor, day, total, lines=lines)


def test_the_items_of_your_last_costco_trip(table):
    receipt_with(table, "c1", "COSTCO WHOLESALE", "2026-09-01", [("KS WATER 40PK", 499)])
    receipt_with(
        table,
        "c2",
        "COSTCO WHOLESALE",
        "2026-09-14",
        [("ROTISSERIE CHICKEN", 499), ("SALMON FILLET", 2489)],
    )

    result = tools.run(ctx(table), "list_bills", {"vendor": "costco", "include_items": True})

    last = result["bills"][0]
    assert last["date"] == "2026-09-14"
    assert last["items"] == [
        {"description": "ROTISSERIE CHICKEN", "amountCents": 499},
        {"description": "SALMON FILLET", "amountCents": 2489},
    ]
    assert "items" not in tools.run(ctx(table), "list_bills", {"vendor": "costco"})["bills"][0]


def test_finding_an_item_lists_every_purchase_newest_first(table):
    receipt_with(
        table, "t1", "TRADER JOE'S", "2026-09-03", [("ORGANIC EGGS 12CT", 349), ("BANANAS", 119)]
    )
    receipt_with(
        table, "t2", "TRADER JOE'S", "2026-10-11", [("ORGANIC EGGS 12CT", 399), ("BAGUETTE", 229)]
    )
    receipt_with(table, "w1", "WHOLE FOODS", "2026-09-20", [("EGG NOODLES", 299)])

    result = tools.run(
        ctx(table, search=lambda q: []), "search_items", {"query": "the price of eggs"}
    )

    assert [(p["date"], p["description"], p["amountCents"]) for p in result["purchases"]] == [
        ("2026-10-11", "ORGANIC EGGS 12CT", 399),
        ("2026-09-20", "EGG NOODLES", 299),
        ("2026-09-03", "ORGANIC EGGS 12CT", 349),
    ]
    assert result["purchases"][0]["vendor"] == "TRADER JOE'S"
    # The change for an item bought more than once is computed here, not by the model.
    assert result["priceChanges"] == [
        {
            "description": "ORGANIC EGGS 12CT",
            "firstDate": "2026-09-03",
            "firstCents": 349,
            "latestDate": "2026-10-11",
            "latestCents": 399,
            "changeCents": 50,
        }
    ]


def test_price_changes_are_capped_newest_first(table):
    for n in range(tools.MAX_PRICE_CHANGES + 2):
        for month in (9, 10):
            day = f"2026-{month:02d}-{n + 1:02d}"
            receipt_with(
                table, f"d{n}-{month}", "MARKET", day, [(f"ORGANIC ITEM {n}", 100 + month)]
            )

    result = tools.run(ctx(table), "search_items", {"query": "organic"})

    assert len(result["priceChanges"]) == tools.MAX_PRICE_CHANGES
    assert result["priceChanges"][0]["description"] == f"ORGANIC ITEM {tools.MAX_PRICE_CHANGES + 1}"


def test_money_you_lent_isnt_a_purchase(table):
    bill(table, "d1", "Concert ticket", "2026-10-04", 5000, kind="owed")
    found = lambda q: [("d1", 1)]  # noqa: E731

    result = tools.run(ctx(table, search=found), "search_items", {"query": "concert ticket"})

    assert result["purchases"] == []


def test_a_line_without_a_description_is_skipped_not_an_error(table):
    lines = [
        {"row": 1, "kind": "item", "amountCents": 100},
        {"row": 2, "description": "BREAD", "kind": "item", "amountCents": 300},
    ]
    bill(table, "d1", "BAKERY", "2026-10-01", 400, lines=lines)

    result = tools.run(ctx(table), "list_bills", {"include_items": True})

    assert result["bills"][0]["items"] == [{"description": "BREAD", "amountCents": 300}]


def test_meaning_search_finds_what_words_miss(table):
    receipt_with(
        table, "b1", "BEST BUY", "2026-09-26", [("USB C CBL 2M", 1999), ("CASE COVER", 1499)]
    )
    search = lambda q: [("b1", 1), ("b1", 3)]  # noqa: E731 - row 3 is the total: not an item

    result = tools.run(ctx(table, search=search), "search_items", {"query": "charging cord"})

    # Only the item the meaning search found, marked so the model can judge it.
    assert [(p["description"], p.get("match")) for p in result["purchases"]] == [
        ("USB C CBL 2M", "meaning")
    ]


def test_an_item_found_by_words_isnt_listed_twice(table):
    receipt_with(table, "b1", "BEST BUY", "2026-09-26", [("USB C CBL 2M", 1999)])

    result = tools.run(ctx(table, search=lambda q: [("b1", 1)]), "search_items", {"query": "usb"})

    assert [p.get("match") for p in result["purchases"]] == [None]
