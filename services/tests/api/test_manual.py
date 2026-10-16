"""POST /documents/manual: a bill typed in by hand, or money someone owes you, saved as a bill."""

from datetime import UTC, datetime

import pytest

from api.documents import manual
from api.documents.logic import BadRequest, QuotaExceeded, get_document, list_documents
from api.documents.splits import set_paid

NOW = datetime(2026, 10, 16, 18, 0, tzinfo=UTC)
BILL = {
    "vendor": "Farmers market",
    "date": "2026-10-15",
    "items": [
        {"description": "Peaches", "amountCents": 600},
        {"description": "Honey", "amountCents": 1200},
    ],
    "taxCents": 0,
    "tipCents": 200,
}


def test_a_typed_bill_is_a_read_bill_with_totals_computed_in_code(table):
    doc = manual.create_bill(table, "user-a", BILL, NOW)

    assert doc["status"] == "ready"
    assert doc["source"] == "manual"
    assert (doc["vendor"], doc["documentDate"], doc["currency"]) == (
        "Farmers market",
        "2026-10-15",
        "USD",
    )
    assert doc["totalCents"] == 2000  # 600 + 1200 + 200 tip
    assert [(ln["description"], ln["kind"], ln["amountCents"]) for ln in doc["lines"]] == [
        ("Peaches", "item", 600),
        ("Honey", "item", 1200),
        ("Tip", "tip", 200),
        ("Total", "total", 2000),
    ]
    assert get_document(table, "user-a", doc["docId"], NOW)["totalCents"] == 2000


def test_a_typed_bill_defaults_to_today_and_needs_only_one_item(table):
    doc = manual.create_bill(
        table,
        "user-a",
        {"vendor": "Cash", "items": [{"description": "Lunch", "amountCents": 1500}]},
        NOW,
    )

    assert doc["documentDate"] == "2026-10-16"
    assert doc["totalCents"] == 1500


@pytest.mark.parametrize(
    "change",
    [
        {"vendor": ""},
        {"items": []},
        {"items": [{"description": "x", "amountCents": 0}]},
        {"items": [{"description": "x", "amountCents": 12.5}]},
        {"items": [{"description": "", "amountCents": 100}]},
        {"items": [{"description": "x", "amountCents": 10_000_001}]},
        {"items": [{"description": "x", "amountCents": 100}] * 41},
        {"vendor": "x" * 1000},
        {"taxCents": -1},
        {"date": "2026-13-01"},
        {"date": "2026-10-20"},  # in the future
        {"currency": "dollars"},
        {"currency": "EUR"},  # one currency until "Owed to you" totals per currency
    ],
)
def test_bad_typed_bills_are_refused(table, change):
    with pytest.raises(BadRequest):
        manual.create_bill(table, "user-a", BILL | change, NOW)


def test_typed_bills_are_capped_per_day(table):
    for _ in range(manual.DAILY_LIMIT):
        manual.create_bill(table, "user-a", BILL, NOW)

    with pytest.raises(QuotaExceeded):
        manual.create_bill(table, "user-a", BILL, NOW)


OWED = {"name": "Minh", "amountCents": 5000, "note": "Concert ticket", "date": "2026-10-14"}


def test_money_someone_owes_you_is_a_one_line_bill_split_to_them(table):
    doc = manual.create_owed(table, "user-a", OWED, NOW)

    assert doc["kind"] == "owed"
    assert (doc["vendor"], doc["totalCents"]) == ("Concert ticket", 5000)
    split = doc["split"]
    assert [(m["name"], m["self"]) for m in split["members"]] == [("Me", True), ("Minh", False)]
    shares = {s["member"]: s["totalCents"] for s in split["shares"]}
    assert shares == {"m0": 0, "m1": 5000}  # all of it on Minh
    summary = list_documents(table, "user-a", now=NOW)[0]
    assert (summary["kind"], summary["person"]) == ("owed", "Minh")
    assert summary["owedCents"] == 5000
    assert summary["owedBy"] == [{"name": "Minh", "cents": 5000}]


def test_ticking_it_paid_clears_what_they_owe(table):
    doc = manual.create_owed(table, "user-a", OWED, NOW)

    set_paid(
        table, "user-a", doc["docId"], {"member": "m1", "paid": True, "expectedVersion": 1}, NOW
    )

    summary = list_documents(table, "user-a", now=NOW)[0]
    assert summary["owedCents"] == 0
    assert "owedBy" not in summary


def test_owed_without_a_note_is_still_named(table):
    doc = manual.create_owed(table, "user-a", {"name": "Bao", "amountCents": 2000}, NOW)

    assert doc["vendor"] == "Money you lent"


@pytest.mark.parametrize(
    "change", [{"name": ""}, {"name": "me"}, {"amountCents": 0}, {"amountCents": "50"}]
)
def test_bad_owed_entries_are_refused(table, change):
    with pytest.raises(BadRequest):
        manual.create_owed(table, "user-a", OWED | change, NOW)


def test_split_bills_list_who_owes_what(table):
    doc = manual.create_bill(table, "user-a", BILL, NOW)
    from api.documents.splits import save_split

    save_split(
        table,
        "user-a",
        doc["docId"],
        {
            "members": [{"name": "Me", "self": True}, {"name": "Linh"}],
            "assignments": {"1": [1], "2": [0]},
            "charges": {"3": {"mode": "proportional"}},
            "expectedVersion": 0,
            "expectedUpdatedAt": doc["updatedAt"],
        },
        NOW,
    )

    summary = list_documents(table, "user-a", now=NOW)[0]
    assert summary["owedBy"] == [{"name": "Linh", "cents": summary["owedCents"]}]
