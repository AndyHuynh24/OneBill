"""PUT /documents/{id}/split: validate against the stored lines, compute shares, store, remember."""

from datetime import UTC, datetime

import pytest

from api.documents.logic import BadRequest, Conflict, NotFound, review_document
from api.documents.splits import save_split
from split.friends import list_friends

NOW = datetime(2026, 10, 1, 19, 0, tzinfo=UTC)
DOC = "1790000000000aaaaaaaa"
LINES = [
    {"row": 1, "description": "PAD THAI", "kind": "item", "amountCents": 1450},
    {"row": 2, "description": "CURRY", "kind": "item", "amountCents": 1200},
    {"row": 3, "description": "ROLLS", "kind": "item", "amountCents": 900},
    {"row": 4, "description": "SUBTOTAL", "kind": "subtotal", "amountCents": 3550},
    {"row": 5, "description": "TAX", "kind": "tax", "amountCents": 296},
    {"row": 6, "description": "TIP", "kind": "tip", "amountCents": 710},
    {"row": 7, "description": "TOTAL", "kind": "total", "amountCents": 4556},
    {"row": 8, "description": "VISA", "kind": "payment", "amountCents": 4556},
]


def put_doc(table, user="user-a", status="ready", lines=LINES, total=4556):
    table.put_item(
        Item={
            "PK": f"USER#{user}",
            "SK": f"DOC#{DOC}",
            "type": "DOCUMENT",
            "docId": DOC,
            "status": status,
            "vendor": "THAI BASIL",
            "totalCents": total,
            "currency": "USD",
            "lines": lines,
            "updatedAt": "2026-10-01T18:00:00+00:00",
        }
    )


def body(**overrides):
    return {
        "members": [{"name": "Me", "self": True}, {"name": "Bao"}, {"name": "Linh"}],
        "assignments": {"1": [1], "2": [2], "3": [0, 1, 2]},
        "charges": {"5": {"mode": "proportional"}, "6": {"mode": "proportional"}},
        "expectedVersion": 0,
    } | overrides


def test_split_is_computed_stored_and_friends_remembered(table):
    put_doc(table)

    split = save_split(table, "user-a", DOC, body(), NOW)

    assert split["version"] == 1
    assert sum(s["totalCents"] for s in split["shares"]) == 4556
    by_name = {m["name"]: m["id"] for m in split["members"]}
    shares = {s["member"]: s for s in split["shares"]}
    assert shares[by_name["Bao"]]["totalCents"] == 2246
    assert shares[by_name["Bao"]]["rows"] == [1, 3]
    assert [m["color"] for m in split["members"]] == [0, 1, 2]  # distinct, by seat
    stored = table.get_item(Key={"PK": "USER#user-a", "SK": f"DOC#{DOC}"})["Item"]["split"]
    assert int(stored["version"]) == 1
    assert sorted(f["name"] for f in list_friends(table, "user-a")) == ["Bao", "Linh"]  # not "Me"


def test_one_person_can_take_the_tip(table):
    put_doc(table)

    split = save_split(
        table,
        "user-a",
        DOC,
        body(charges={"5": {"mode": "even"}, "6": {"mode": "person", "member": 0}}),
        NOW,
    )

    me = next(s for s in split["shares"] if s["member"] == split["members"][0]["id"])
    assert me["totalCents"] == 300 + 99 + 710  # rolls share + a third of tax (rounded) + tip


def test_resaving_needs_the_current_version(table):
    put_doc(table)
    save_split(table, "user-a", DOC, body(), NOW)

    with pytest.raises(Conflict):
        save_split(table, "user-a", DOC, body(expectedVersion=0), NOW)  # stale

    assert save_split(table, "user-a", DOC, body(expectedVersion=1), NOW)["version"] == 2


def test_a_bill_that_doesnt_add_up_must_be_fixed_first(table):
    put_doc(table, status="needs_review", total=5000)

    with pytest.raises(BadRequest, match="add up"):
        save_split(table, "user-a", DOC, body(), NOW)


def test_someone_elses_document_is_not_found(table):
    put_doc(table, user="user-b")

    with pytest.raises(NotFound):
        save_split(table, "user-a", DOC, body(), NOW)


@pytest.mark.parametrize(
    "overrides",
    [
        {"members": [{"name": "Me", "self": True}]},  # a split needs at least 2 people
        {"members": [{"name": "Me", "self": True}] + [{"name": f"P{i}"} for i in range(12)]},
        {"members": [{"name": "Me", "self": True}, {"name": "Bao"}, {"name": "bao"}]},
        {"members": [{"name": "Me"}, {"name": "Bao"}, {"name": "Linh"}]},  # nobody is "self"
        {"members": [{"name": "", "self": True}, {"name": "Bao"}, {"name": "Linh"}]},
        {"members": "Me, Bao"},
        {"assignments": {"1": [7], "2": [2], "3": [0]}},  # no such person
        {"assignments": {"x": [1], "2": [2], "3": [0]}},
        {"assignments": {"1": [1], "2": [2]}},  # rolls unassigned
        {"assignments": {"1": [1], "2": [2], "3": [0], "8": [0]}},  # payment isn't an item
        {"charges": {"5": {"mode": "proportional"}}},  # tip without a choice
        {"charges": {"5": {"mode": "random"}, "6": {"mode": "even"}}},
        {"charges": {"5": {"mode": "person", "member": 9}, "6": {"mode": "even"}}},
        {"expectedVersion": "1"},
    ],
)
def test_invalid_splits_are_rejected(table, overrides):
    put_doc(table)

    with pytest.raises(BadRequest):
        save_split(table, "user-a", DOC, body(**overrides), NOW)


def test_fixing_lines_clears_an_existing_split(table):
    """Rows may change when lines are edited, so an old split can't be trusted."""
    put_doc(table)
    save_split(table, "user-a", DOC, body(), NOW)
    lines = [{k: v for k, v in line.items() if k != "row"} for line in LINES]

    doc = review_document(
        table, "user-a", DOC, {"action": "correct", "fields": {"lines": lines}}, NOW
    )

    assert "split" not in doc
