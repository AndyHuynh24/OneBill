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


def test_search_returns_the_matching_bills_with_their_lines(table):
    bill(table, "d1", "BEST BUY", "2026-08-02", 1999)
    bill(table, "d2", "TARGET", "2026-09-02", 500)
    seen = []

    def search(query):
        seen.append(query)
        return ["d1", "missing"]  # a deleted bill can still be in the index briefly

    result = tools.run(ctx(table, search=search), "search_items", {"query": "usb-c cable"})

    assert seen == ["usb-c cable"]
    (hit,) = result["matches"]
    assert (hit["docId"], hit["vendor"], hit["totalCents"]) == ("d1", "BEST BUY", 1999)
    assert hit["lines"] == [{"description": "BEST BUY ITEM", "amountCents": 1999}]


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
