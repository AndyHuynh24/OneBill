"""Trips: start → scan from the trip tile → split (anyone can pay) → settle up → end."""

from datetime import UTC, datetime

import boto3
import pytest

from api.documents import trips
from api.documents.logic import BadRequest, Conflict, NotFound, create_upload, list_documents
from api.documents.splits import save_split

BUCKET = "onebill-docs-test"
NOW = datetime(2026, 10, 3, 18, 0, tzinfo=UTC)
LATER = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)
PEOPLE = [{"name": "Me", "self": True}, {"name": "Minh"}, {"name": "Linh"}]


@pytest.fixture
def s3(table):
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket=BUCKET)
    return client


def start(table, user="user-a", name="Chicago", members=PEOPLE):
    return trips.create_trip(table, user, {"name": name, "members": members}, NOW)


def upload(table, s3, user="user-a", when=NOW, trip=None):
    """A scan; `trip` is the trip tile's own Scan button (the main button sends none)."""
    return create_upload(
        table, s3, BUCKET, user, set(), "r.jpg", "image/jpeg", 1000, now=when, trip_id=trip
    )["docId"]


def ready_receipt(table, doc_id, total, user="user-a"):
    """What the extract Lambda leaves behind: a read receipt with one item and a total."""
    table.update_item(
        Key={"PK": f"USER#{user}", "SK": f"DOC#{doc_id}"},
        UpdateExpression="SET #s = :s, totalCents = :t, #l = :l, currency = :c, vendor = :v",
        ExpressionAttributeNames={"#s": "status", "#l": "lines"},
        ExpressionAttributeValues={
            ":s": "ready",
            ":t": total,
            ":c": "USD",
            ":v": "DINER",
            ":l": [
                {"row": 1, "description": "FOOD", "kind": "item", "amountCents": total},
                {"row": 2, "description": "TOTAL", "kind": "total", "amountCents": total},
            ],
        },
    )


def split_evenly(table, doc_id, paid_by=0, people=PEOPLE, user="user-a"):
    doc = table.get_item(Key={"PK": f"USER#{user}", "SK": f"DOC#{doc_id}"})["Item"]
    body = {
        "members": people,
        "assignments": {"1": list(range(len(people)))},
        "charges": {},
        "expectedVersion": 0,
        "expectedUpdatedAt": doc["updatedAt"],
        "paidBy": paid_by,
    }
    return save_split(table, user, doc_id, body, NOW)


def tick_minh(table, trip, paid=True):
    body = {"from": "Minh", "to": "Me", "paid": paid}
    return trips.set_settled(table, "user-a", trip["tripId"], body, LATER)


# --- starting and listing ------------------------------------------------------------------------


def test_starting_a_trip_makes_it_the_active_one(table):
    trip = start(table)

    assert trip["name"] == "Chicago"
    assert trip["status"] == "active"
    assert [m["name"] for m in trip["members"]] == ["Me", "Minh", "Linh"]
    assert trips.list_trips(table, "user-a")["active"]["tripId"] == trip["tripId"]


def test_only_one_trip_can_be_active(table):
    start(table)

    with pytest.raises(Conflict, match="already"):
        start(table, name="Denver")


@pytest.mark.parametrize(
    "body",
    [
        {"name": "", "members": PEOPLE},
        {"name": "x" * 61, "members": PEOPLE},
        {"name": "Chicago", "members": [{"name": "Me", "self": True}]},  # nobody to split with
        {"name": "Chicago", "members": [{"name": "Me"}, {"name": "Minh"}]},  # you aren't in it
        {"name": "Chicago", "members": [*PEOPLE, {"name": "minh"}]},  # Minh twice
    ],
)
def test_a_bad_trip_is_refused(table, body):
    with pytest.raises(BadRequest):
        trips.create_trip(table, "user-a", body, NOW)


def test_trips_are_private(table):
    trip = start(table)

    assert trips.list_trips(table, "user-b") == {"active": None, "past": []}
    with pytest.raises(NotFound):
        trips.get_trip(table, "user-b", trip["tripId"])


# --- receipts: scanned from the trip tile, or added afterwards -----------------------------------


def test_a_scan_from_the_trip_tile_joins_that_trip(table, s3):
    trip = start(table)

    doc_id = upload(table, s3, trip=trip["tripId"])

    docs = {d["docId"]: d for d in list_documents(table, "user-a", now=NOW)}
    assert docs[doc_id]["tripId"] == trip["tripId"]
    receipts = trips.get_trip(table, "user-a", trip["tripId"])["receipts"]
    assert [r["docId"] for r in receipts] == [doc_id]


def test_the_main_scan_button_never_joins_a_trip_even_while_one_is_on(table, s3):
    start(table)

    upload(table, s3)

    assert "tripId" not in list_documents(table, "user-a", now=NOW)[0]


def test_a_scan_into_an_ended_trip_is_refused_before_using_a_scan(table, s3):
    trip = start(table)
    trips.update_trip(table, "user-a", trip["tripId"], {"end": True}, LATER)

    with pytest.raises(BadRequest, match="isn't on"):
        upload(table, s3, trip=trip["tripId"], when=LATER)
    assert list_documents(table, "user-a", now=LATER) == []


def test_a_scan_cant_go_into_someone_elses_trip(table, s3):
    theirs = start(table, user="user-b")

    with pytest.raises(BadRequest):
        upload(table, s3, trip=theirs["tripId"])


def test_a_receipt_can_be_added_to_a_past_trip(table, s3):
    trip = start(table)
    trips.update_trip(table, "user-a", trip["tripId"], {"end": True}, LATER)
    doc_id = upload(table, s3, when=LATER)

    trips.add_document(table, "user-a", trip["tripId"], doc_id)

    assert len(trips.get_trip(table, "user-a", trip["tripId"])["receipts"]) == 1


def test_a_receipt_can_be_removed_from_and_added_to_a_trip(table, s3):
    trip = start(table)
    doc_id = upload(table, s3, trip=trip["tripId"])

    trips.remove_document(table, "user-a", trip["tripId"], doc_id)
    assert trips.get_trip(table, "user-a", trip["tripId"])["receipts"] == []

    trips.add_document(table, "user-a", trip["tripId"], doc_id)
    assert len(trips.get_trip(table, "user-a", trip["tripId"])["receipts"]) == 1


def test_you_cant_add_someone_elses_receipt(table, s3):
    trip = start(table)
    theirs = upload(table, s3, user="user-b")

    with pytest.raises(NotFound):
        trips.add_document(table, "user-a", trip["tripId"], theirs)


# --- settle up -----------------------------------------------------------------------------------


def test_the_trip_shows_totals_and_the_fewest_payments(table, s3):
    trip = start(table)
    dinner = upload(table, s3, trip=trip["tripId"])
    gas = upload(table, s3, when=LATER, trip=trip["tripId"])
    ready_receipt(table, dinner, 9000)
    ready_receipt(table, gas, 6000)
    split_evenly(table, dinner, paid_by=0)  # I paid dinner: 30 each
    split_evenly(table, gas, paid_by=1)  # Minh paid gas: 20 each

    view = trips.get_trip(table, "user-a", trip["tripId"])

    assert view["totalCents"] == 15000
    assert view["spent"] == {"Me": 5000, "Minh": 5000, "Linh": 5000}
    assert view["payments"] == [
        {"from": "Linh", "to": "Me", "amountCents": 4000, "settledAt": None},
        {"from": "Linh", "to": "Minh", "amountCents": 1000, "settledAt": None},
    ]
    assert [r["paidBy"] for r in view["receipts"]] == ["Minh", "Me"]  # newest first


def test_unsplit_receipts_are_listed_but_not_counted(table, s3):
    trip = start(table)
    dinner, lunch = upload(table, s3, trip=trip["tripId"]), upload(table, s3, trip=trip["tripId"])
    ready_receipt(table, dinner, 9000)
    ready_receipt(table, lunch, 3000)
    split_evenly(table, dinner)

    view = trips.get_trip(table, "user-a", trip["tripId"])

    assert view["unsplit"] == [lunch]
    assert view["totalCents"] == 12000  # the trip's spending includes it…
    assert sum(p["amountCents"] for p in view["payments"]) == 6000  # …the balances don't


def test_a_payment_can_be_ticked_and_unticked(table, s3):
    trip = start(table)
    dinner = upload(table, s3, trip=trip["tripId"])
    ready_receipt(table, dinner, 9000)
    split_evenly(table, dinner)

    trips.set_settled(
        table, "user-a", trip["tripId"], {"from": "Minh", "to": "Me", "paid": True}, LATER
    )
    view = trips.get_trip(table, "user-a", trip["tripId"])
    minh = next(p for p in view["payments"] if p["from"] == "Minh")
    assert minh == {
        "from": "Minh",
        "to": "Me",
        "amountCents": 3000,
        "settledAt": LATER.isoformat(),
    }

    trips.set_settled(
        table, "user-a", trip["tripId"], {"from": "Minh", "to": "Me", "paid": False}, LATER
    )
    after = trips.get_trip(table, "user-a", trip["tripId"])["payments"]
    assert all(p["settledAt"] is None for p in after)


def test_a_tick_only_counts_while_that_payment_is_unchanged(table, s3):
    trip = start(table)
    dinner, drinks = upload(table, s3, trip=trip["tripId"]), upload(table, s3, trip=trip["tripId"])
    ready_receipt(table, dinner, 9000)
    split_evenly(table, dinner)
    trips.set_settled(
        table, "user-a", trip["tripId"], {"from": "Minh", "to": "Me", "paid": True}, LATER
    )

    ready_receipt(table, drinks, 3000)
    split_evenly(table, drinks)  # Minh now owes me more

    view = trips.get_trip(table, "user-a", trip["tripId"])
    minh = next(p for p in view["payments"] if p["from"] == "Minh")
    assert (minh["amountCents"], minh["settledAt"]) == (4000, None)
    assert view["balancesChanged"] is True


def test_only_a_current_payment_can_be_ticked(table, s3):
    trip = start(table)

    with pytest.raises(BadRequest, match="no longer"):
        trips.set_settled(
            table, "user-a", trip["tripId"], {"from": "Minh", "to": "Me", "paid": True}, LATER
        )


# --- changing and ending -------------------------------------------------------------------------


def test_ending_a_trip_moves_it_to_past_trips(table):
    trip = start(table)

    ended = trips.update_trip(table, "user-a", trip["tripId"], {"end": True}, LATER)

    assert ended["status"] == "ended"
    assert ended["endedAt"] == LATER.isoformat()
    listed = trips.list_trips(table, "user-a")
    assert listed["active"] is None
    assert [t["tripId"] for t in listed["past"]] == [trip["tripId"]]
    start(table, name="Denver")  # a new one can start


def test_a_trip_can_be_renamed_and_people_added(table):
    trip = start(table)

    updated = trips.update_trip(
        table,
        "user-a",
        trip["tripId"],
        {"name": "Chi-town", "members": [*PEOPLE, {"name": "Bao"}]},
        LATER,
    )

    assert updated["name"] == "Chi-town"
    assert [m["name"] for m in updated["members"]] == ["Me", "Minh", "Linh", "Bao"]


def test_deleting_a_trip_keeps_its_receipts(table, s3):
    trip = start(table)
    doc_id = upload(table, s3, trip=trip["tripId"])

    trips.delete_trip(table, "user-a", trip["tripId"])

    assert trips.list_trips(table, "user-a") == {"active": None, "past": []}
    (doc,) = list_documents(table, "user-a", now=NOW)
    assert doc["docId"] == doc_id
    assert "tripId" not in doc


# --- trip receipts on Home ---------------------------------------------------------------------


def test_trip_receipts_show_no_per_receipt_owed_amount(table, s3):
    trip = start(table)
    dinner = upload(table, s3, trip=trip["tripId"])
    ready_receipt(table, dinner, 9000)
    split_evenly(table, dinner)

    (doc,) = list_documents(table, "user-a", now=NOW)
    assert "owedCents" not in doc  # the trip settles as a whole


def test_paid_by_must_be_someone_in_the_split(table, s3):
    dinner = upload(table, s3)
    ready_receipt(table, dinner, 9000)

    with pytest.raises(BadRequest, match="Paid by"):
        split_evenly(table, dinner, paid_by=7)


# --- edge cases ----------------------------------------------------------------------------------


def test_a_scan_doesnt_join_a_trip_that_ended_while_it_was_uploading(table, s3, monkeypatch):
    trip = start(table)
    trips.update_trip(table, "user-a", trip["tripId"], {"end": True}, LATER)
    # The upload checked the trip just before it ended.
    monkeypatch.setattr("api.documents.trip_keys.is_active", lambda *_: True)

    doc_id = upload(table, s3, when=LATER, trip=trip["tripId"])

    item = table.get_item(Key={"PK": "USER#user-a", "SK": f"DOC#{doc_id}"})["Item"]
    assert "tripId" not in item
    assert "GSI2PK" not in item


def test_a_trip_can_still_end_and_be_deleted_if_its_pointer_is_gone(table):
    trip = start(table)
    table.delete_item(Key={"PK": "USER#user-a", "SK": "ACTIVE_TRIP"})

    assert (
        trips.update_trip(table, "user-a", trip["tripId"], {"end": True}, LATER)["status"]
        == "ended"
    )
    trips.delete_trip(table, "user-a", trip["tripId"])
    assert trips.list_trips(table, "user-a") == {"active": None, "past": []}


def test_past_trips_are_listed_without_totals(table, s3):
    trip = start(table)
    upload(table, s3, trip=trip["tripId"])
    trips.update_trip(table, "user-a", trip["tripId"], {"end": True}, LATER)

    (past,) = trips.list_trips(table, "user-a")["past"]
    assert "receiptCount" not in past  # computed only for the trip that's on


def test_a_trip_holds_at_most_100_receipts(table, s3, monkeypatch):
    trip = start(table)
    doc_id = upload(table, s3)
    monkeypatch.setattr(trips, "MAX_TRIP_RECEIPTS", 0)

    with pytest.raises(BadRequest, match="at most"):
        trips.add_document(table, "user-a", trip["tripId"], doc_id)
