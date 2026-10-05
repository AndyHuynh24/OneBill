"""PUT /documents/{id}/split: validate against the stored lines, compute shares, store, remember."""

from datetime import UTC, datetime

import pytest

from api.documents.logic import BadRequest, Conflict, NotFound, review_document
from api.documents.splits import save_split

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


SEEN = "2026-10-01T18:00:00+00:00"  # the document version the client rendered


def body(**overrides):
    return {
        "members": [{"name": "Me", "self": True}, {"name": "Bao"}, {"name": "Linh"}],
        "assignments": {"1": [1], "2": [2], "3": [0, 1, 2]},
        "charges": {"5": {"mode": "proportional"}, "6": {"mode": "proportional"}},
        "expectedVersion": 0,
        "expectedUpdatedAt": SEEN,
    } | overrides


def test_split_is_computed_and_stored(table):
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
    assert "friendId" not in stored["members"][1]  # friends live only on the device


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

    resaved = save_split(
        table, "user-a", DOC, body(expectedVersion=1, expectedUpdatedAt=NOW.isoformat()), NOW
    )
    assert resaved["version"] == 2


def test_a_bill_that_doesnt_add_up_is_split_on_the_recorded_items(table):
    put_doc(table, status="needs_review", total=5000)  # printed total is a misread

    split = save_split(table, "user-a", DOC, body(), NOW)

    assert split["totalCents"] == 4556  # what the recorded lines add up to
    assert split["receiptTotalCents"] == 5000
    assert split["matchesReceipt"] is False
    assert sum(s["totalCents"] for s in split["shares"]) == 4556


def test_a_tax_inclusive_bill_doesnt_need_a_choice_for_its_tax(table):
    lines = [
        {"row": 1, "description": "COMBO", "kind": "item", "amountCents": 8700000},
        {"row": 2, "description": "TEA", "kind": "item", "amountCents": 1200000},
        {"row": 3, "description": "SUBTOTAL", "kind": "subtotal", "amountCents": 9166667},
        {"row": 4, "description": "VAT", "kind": "tax", "amountCents": 733333},
        {"row": 5, "description": "TOTAL", "kind": "total", "amountCents": 9900000},
    ]
    put_doc(table, lines=lines, total=9900000)
    table.update_item(
        Key={"PK": "USER#user-a", "SK": f"DOC#{DOC}"},
        UpdateExpression="SET taxIncluded = :t",
        ExpressionAttributeValues={":t": True},
    )

    split = save_split(
        table, "user-a", DOC, body(assignments={"1": [1], "2": [2]}, charges={}), NOW
    )

    assert split["matchesReceipt"] is True
    assert [s["totalCents"] for s in split["shares"]] == [0, 8700000, 1200000]


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


def test_a_split_made_on_lines_that_changed_since_is_refused(table):
    """Security review: rows are renumbered when lines are edited on another device; a split
    computed on the old lines must not land on different items."""
    put_doc(table)

    with pytest.raises(Conflict):
        save_split(table, "user-a", DOC, body(expectedUpdatedAt="2026-10-01T17:00:00+00:00"), NOW)


def test_the_document_version_the_client_saw_is_required(table):
    put_doc(table)

    with pytest.raises(BadRequest):
        save_split(
            table, "user-a", DOC, {k: v for k, v in body().items() if k != "expectedUpdatedAt"}, NOW
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"assignments": {"1": [0] * 13, "2": [2], "3": [0]}},
        {"assignments": {str(i): [0] for i in range(1, 202)}},
        {"charges": {str(i): {"mode": "even"} for i in range(1, 202)}},
    ],
)
def test_oversized_requests_are_rejected_before_any_work(table, overrides):
    put_doc(table)

    with pytest.raises(BadRequest):
        save_split(table, "user-a", DOC, body(**overrides), NOW)


def test_correcting_only_the_total_also_clears_the_split(table):
    put_doc(table)
    save_split(table, "user-a", DOC, body(), NOW)

    doc = review_document(
        table, "user-a", DOC, {"action": "correct", "fields": {"totalCents": 4600}}, NOW
    )

    assert "split" not in doc


def test_a_charge_can_be_shared_by_some_people_like_an_item(table):
    put_doc(table)

    split = save_split(
        table,
        "user-a",
        DOC,
        body(charges={"5": {"mode": "proportional"}, "6": {"mode": "people", "members": [1, 2]}}),
        NOW,
    )

    ids = [m["id"] for m in split["members"]]
    assert split["charges"]["6"] == {"mode": "people", "members": [ids[1], ids[2]]}
    me = next(s for s in split["shares"] if s["member"] == ids[0])
    assert me["extrasCents"] < 710  # Me doesn't share the tip
