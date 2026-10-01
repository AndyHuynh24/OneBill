import base64
import json
from datetime import UTC, date, datetime

import boto3
import pytest

from api.documents import logic
from api.documents.logic import (
    BadRequest,
    Forbidden,
    NotFound,
    QuotaExceeded,
    create_upload,
    get_document,
    list_documents,
    list_obligations,
    parse_groups,
)

BUCKET = "onetally-docs-test"
NOW = datetime(2026, 9, 30, 18, 0, 0, tzinfo=UTC)
UPLOADER = {"uploaders"}


@pytest.fixture
def s3(table):
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket=BUCKET)
    return client


def upload(table, s3, user="user-a", groups=UPLOADER, **overrides):
    args = {
        "filename": "receipt.jpg",
        "content_type": "image/jpeg",
        "size_bytes": 2_000_000,
    } | overrides
    return create_upload(table, s3, BUCKET, user, groups, now=NOW, **args)


def policy_conditions(fields: dict) -> list:
    return json.loads(base64.b64decode(fields["policy"]))["conditions"]


# --- POST /documents ---------------------------------------------------------------------------


def test_create_upload_returns_locked_down_presigned_post(table, s3):
    result = upload(table, s3)

    doc_id = result["docId"]
    fields = result["upload"]["fields"]
    assert result["upload"]["url"].startswith("https://")
    assert fields["key"] == f"uploads/user-a/{doc_id}"
    assert fields["Content-Type"] == "image/jpeg"
    conditions = policy_conditions(fields)
    assert ["content-length-range", 1, logic.MAX_BYTES] in conditions
    assert {"Content-Type": "image/jpeg"} in conditions


def test_create_upload_records_pending_document(table, s3):
    doc_id = upload(table, s3)["docId"]

    item = table.get_item(Key={"PK": "USER#user-a", "SK": f"DOC#{doc_id}"})["Item"]
    assert item["status"] == "pending_upload"
    assert item["s3Key"] == f"uploads/user-a/{doc_id}"
    assert item["contentType"] == "image/jpeg"
    assert item["filename"] == "receipt.jpg"


def test_only_uploaders_may_upload(table, s3):
    with pytest.raises(Forbidden):
        upload(table, s3, groups=set())

    usage = table.get_item(Key={"PK": "USER#user-a", "SK": "USAGE#2026-09-30"}).get("Item")
    assert usage is None  # no quota consumed by a refused request


@pytest.mark.parametrize(
    "overrides",
    [
        {"content_type": "image/gif"},
        {"content_type": "text/html"},
        {"size_bytes": 0},
        {"size_bytes": 10 * 1024 * 1024 + 1},
        {"filename": ""},
    ],
)
def test_rejects_bad_uploads(table, s3, overrides):
    with pytest.raises(BadRequest):
        upload(table, s3, **overrides)


def test_filename_is_reduced_to_a_safe_display_name(table, s3):
    doc_id = upload(
        table, s3, filename="../../secret/" + "x" * 300 + ".pdf", content_type="application/pdf"
    )["docId"]

    item = table.get_item(Key={"PK": "USER#user-a", "SK": f"DOC#{doc_id}"})["Item"]
    assert "/" not in item["filename"]
    assert len(item["filename"]) <= logic.MAX_FILENAME


def test_per_user_daily_quota(table, s3):
    for _ in range(logic.USER_DAILY_LIMIT):
        upload(table, s3)

    with pytest.raises(QuotaExceeded):
        upload(table, s3)


def test_decision_r2_limits():
    assert (logic.USER_DAILY_LIMIT, logic.GLOBAL_DAILY_LIMIT) == (10, 15)


def test_global_daily_quota_refunds_the_user_counter(table, s3):
    # Fill the global cap using several users, none of them over their own limit.
    for i in range(logic.GLOBAL_DAILY_LIMIT):
        upload(table, s3, user=f"user-{i // logic.USER_DAILY_LIMIT}")

    with pytest.raises(QuotaExceeded):
        upload(table, s3, user="someone-new")

    usage = table.get_item(Key={"PK": "USER#someone-new", "SK": "USAGE#2026-09-30"})["Item"]
    assert usage["uploads"] == 0  # the per-user slot was given back


def test_usage_counters_expire(table, s3):
    upload(table, s3)

    usage = table.get_item(Key={"PK": "USER#user-a", "SK": "USAGE#2026-09-30"})["Item"]
    assert usage["expiresAt"] > int(NOW.timestamp())


# --- GET /documents, GET /documents/{id} --------------------------------------------------------


def test_list_documents_newest_first_and_only_mine(table, s3):
    first = create_upload(
        table, s3, BUCKET, "user-a", UPLOADER, "a.jpg", "image/jpeg", 100, now=NOW
    )["docId"]
    second = create_upload(
        table,
        s3,
        BUCKET,
        "user-a",
        UPLOADER,
        "b.jpg",
        "image/jpeg",
        100,
        now=datetime(2026, 9, 30, 18, 0, 1, tzinfo=UTC),
    )["docId"]
    upload(table, s3, user="user-b")

    docs = list_documents(table, "user-a", now=NOW)

    assert [d["docId"] for d in docs] == [second, first]
    assert all(d["status"] == "pending_upload" for d in docs)


def test_get_document_returns_my_document(table, s3):
    doc_id = upload(table, s3)["docId"]

    doc = get_document(table, "user-a", doc_id, now=NOW)

    assert doc["docId"] == doc_id
    assert "PK" not in doc
    assert "s3Key" not in doc  # storage details stay server-side


def test_cannot_read_someone_elses_document(table, s3):
    doc_id = upload(table, s3, user="user-b")["docId"]

    with pytest.raises(NotFound):
        get_document(table, "user-a", doc_id)


@pytest.mark.parametrize("bad_id", ["", "../x", "DOC#123", "12345"])
def test_malformed_document_id_is_not_found(table, bad_id):
    with pytest.raises(NotFound):
        get_document(table, "user-a", bad_id)


# --- GET /obligations ---------------------------------------------------------------------------


def put_obligation(table, user, due, obl_id, **extra):
    table.put_item(
        Item={
            "PK": f"USER#{user}",
            "SK": f"OBL#{due}#{obl_id}",
            "type": "OBLIGATION",
            "oblId": obl_id,
            "dueDate": due,
            "kind": "return_by",
            "vendor": "Target",
            "docId": "1790000000000aaaaaaaa",
        }
        | extra
    )


def test_list_obligations_within_window(table):
    put_obligation(table, "user-a", "2026-09-29", "o0")  # past
    put_obligation(table, "user-a", "2026-09-30", "o1")  # today
    put_obligation(table, "user-a", "2026-10-15", "o2")
    put_obligation(table, "user-a", "2026-10-31", "o3")  # outside a 30-day window
    put_obligation(table, "user-b", "2026-10-01", "o4")  # someone else's

    found = list_obligations(table, "user-a", today=date(2026, 9, 30), within_days=30)

    assert [o["oblId"] for o in found] == ["o1", "o2"]


def test_list_obligations_caps_the_window(table):
    with pytest.raises(BadRequest):
        list_obligations(table, "user-a", today=date(2026, 9, 30), within_days=400)


# --- group claim parsing ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("claim", "expected"),
    [
        (["uploaders", "admins"], {"uploaders", "admins"}),
        ("[uploaders admins]", {"uploaders", "admins"}),  # how HTTP API flattens array claims
        ("uploaders", {"uploaders"}),
        ("[]", set()),
        (None, set()),
    ],
)
def test_parse_groups(claim, expected):
    assert parse_groups(claim) == expected


# --- PATCH /documents/{id} (review) -------------------------------------------------------------

from api.documents.logic import review_document  # noqa: E402


def processed_doc(table, user="user-a", status="needs_review", obligations=("2026-10-28",)):
    doc_id = "1790000000000aaaaaaaa"
    keys = []
    for i, due in enumerate(obligations):
        sk = f"OBL#{due}#17900000000{i:02d}bbbbbbbb"
        keys.append(sk)
        table.put_item(
            Item={
                "PK": f"USER#{user}",
                "SK": sk,
                "type": "OBLIGATION",
                "oblId": sk.rsplit("#", 1)[1],
                "docId": doc_id,
                "kind": "return_by",
                "dueDate": due,
                "remindersSent": [7],
                "GSI1PK": f"DUE#{due}",
                "GSI1SK": f"USER#{user}#OBL#{sk.rsplit('#', 1)[1]}",
            }
        )
    table.put_item(
        Item={
            "PK": f"USER#{user}",
            "SK": f"DOC#{doc_id}",
            "type": "DOCUMENT",
            "docId": doc_id,
            "status": status,
            "vendor": "TARGT",
            "documentDate": "2026-09-28",
            "totalCents": 2618,
            "currency": "USD",
            "issues": ["The total was hard to read (OCR confidence 62%). Please check it."],
            "obligationKeys": keys,
            "createdAt": "2026-09-28T10:00:00+00:00",
        }
    )
    return doc_id


def obligations_for(table, user="user-a"):
    return table.query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :p)",
        ExpressionAttributeValues={":pk": f"USER#{user}", ":p": "OBL#"},
    )["Items"]


def test_accept_marks_ready_and_records_outcome(table):
    doc_id = processed_doc(table)

    doc = review_document(table, "user-a", doc_id, {"action": "accept"}, now=NOW)

    assert doc["status"] == "ready"
    assert doc["reviewOutcome"] == "accepted"
    assert doc["reviewedAt"] == NOW.isoformat()
    assert len(obligations_for(table)) == 1  # untouched


def test_correct_updates_fields_and_records_what_changed(table):
    doc_id = processed_doc(table)

    doc = review_document(
        table,
        "user-a",
        doc_id,
        {"action": "correct", "fields": {"vendor": "Target", "totalCents": 2681}},
        now=NOW,
    )

    assert doc["status"] == "ready"
    assert doc["vendor"] == "Target"
    assert doc["totalCents"] == 2681
    assert doc["reviewOutcome"] == "corrected"
    assert sorted(doc["correctedFields"]) == ["totalCents", "vendor"]


def test_correcting_deadlines_replaces_obligations_and_their_index_entries(table):
    doc_id = processed_doc(table)

    review_document(
        table,
        "user-a",
        doc_id,
        {
            "action": "correct",
            "fields": {"deadlines": [{"kind": "return_by", "dueDate": "2026-11-15"}]},
        },
        now=NOW,
    )

    remaining = obligations_for(table)
    assert [o["dueDate"] for o in remaining] == ["2026-11-15"]
    assert remaining[0]["GSI1PK"] == "DUE#2026-11-15"
    assert remaining[0]["remindersSent"] == []
    assert remaining[0]["evidence"] == "Entered during review"
    item = table.get_item(Key={"PK": "USER#user-a", "SK": f"DOC#{doc_id}"})["Item"]
    assert item["obligationKeys"] == [remaining[0]["SK"]]


def test_removing_all_deadlines(table):
    doc_id = processed_doc(table)

    review_document(
        table, "user-a", doc_id, {"action": "correct", "fields": {"deadlines": []}}, now=NOW
    )

    assert obligations_for(table) == []


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"action": "delete"},
        {"action": "correct"},
        {"action": "correct", "fields": {}},
        {"action": "correct", "fields": {"status": "ready"}},  # not editable
        {"action": "correct", "fields": {"documentDate": "28/09/2026"}},
        {"action": "correct", "fields": {"totalCents": 12.5}},
        {"action": "correct", "fields": {"totalCents": True}},
        {"action": "correct", "fields": {"currency": "dollars"}},
        {"action": "correct", "fields": {"vendor": "x" * 201}},
        {
            "action": "correct",
            "fields": {"deadlines": [{"kind": "someday", "dueDate": "2026-11-15"}]},
        },
        {"action": "correct", "fields": {"deadlines": [{"kind": "return_by", "dueDate": "soon"}]}},
    ],
)
def test_review_rejects_invalid_requests(table, body):
    doc_id = processed_doc(table)

    with pytest.raises(BadRequest):
        review_document(table, "user-a", doc_id, body, now=NOW)


@pytest.mark.parametrize("status", ["pending_upload", "processing", "failed"])
def test_cannot_review_a_document_that_is_not_processed(table, status):
    doc_id = processed_doc(table, status=status)

    with pytest.raises(BadRequest):
        review_document(table, "user-a", doc_id, {"action": "accept"}, now=NOW)


def test_cannot_review_someone_elses_document(table):
    doc_id = processed_doc(table, user="user-b")

    with pytest.raises(NotFound):
        review_document(table, "user-a", doc_id, {"action": "accept"}, now=NOW)


def test_list_documents_reports_acceptance_rate(table):
    for i, outcome in enumerate(["accepted", "accepted", "corrected", None]):
        item = {
            "PK": "USER#user-a",
            "SK": f"DOC#17900000000{i:02d}aaaaaaaa",
            "docId": f"17900000000{i:02d}aaaaaaaa",
            "status": "ready",
        }
        if outcome:
            item["reviewOutcome"] = outcome
        table.put_item(Item=item)

    from api.documents.logic import review_stats

    assert review_stats(list_documents(table, "user-a")) == {
        "reviewed": 3,
        "acceptedUnchanged": 2,
        "acceptanceRate": 0.67,
    }


# --- review-round fixes -------------------------------------------------------------------------

from datetime import timedelta  # noqa: E402


def stale_item(table, status, created, started=None):
    item = {
        "PK": "USER#user-a",
        "SK": "DOC#1790000000000cccccccc",
        "docId": "1790000000000cccccccc",
        "status": status,
        "createdAt": created.isoformat(),
    }
    if started:
        item["processingStartedAt"] = started.isoformat()
    table.put_item(Item=item)


def test_abandoned_upload_is_reported_as_failed(table):
    stale_item(table, "pending_upload", created=NOW - timedelta(minutes=20))

    [doc] = list_documents(table, "user-a", now=NOW)

    assert doc["status"] == "failed"
    assert "didn't finish" in doc["failureReason"]


def test_recent_upload_is_still_pending(table):
    stale_item(table, "pending_upload", created=NOW - timedelta(minutes=2))

    [doc] = list_documents(table, "user-a", now=NOW)

    assert doc["status"] == "pending_upload"


def test_stuck_processing_is_reported_as_failed(table):
    stale_item(
        table, "processing", created=NOW - timedelta(hours=1), started=NOW - timedelta(minutes=20)
    )

    doc = get_document(table, "user-a", "1790000000000cccccccc", now=NOW)

    assert doc["status"] == "failed"
    assert "too long" in doc["failureReason"]


def test_concurrent_corrections_cannot_orphan_obligations(table):
    doc_id = processed_doc(table)
    stale_keys = table.get_item(Key={"PK": "USER#user-a", "SK": f"DOC#{doc_id}"})["Item"][
        "obligationKeys"
    ]
    body = {
        "action": "correct",
        "fields": {"deadlines": [{"kind": "return_by", "dueDate": "2026-11-15"}]},
    }
    review_document(table, "user-a", doc_id, body, now=NOW)

    # A second save that read the document *before* the first one finished:
    with pytest.raises(BadRequest, match="changed"):
        logic._write_review(
            table,
            "user-a",
            doc_id,
            {"reviewOutcome": "corrected", "obligationKeys": []},
            stale_keys,
            [],
            expected_keys=stale_keys,
        )

    assert len(obligations_for(table)) == 1


# --- step 6: fixing individual lines ---------------------------------------------------------

DINNER = [
    {"description": "PAD THAI", "kind": "item", "amountCents": 1450},
    {"description": "CURRY", "kind": "item", "amountCents": 1200},
    {"description": "SUBTOTAL", "kind": "subtotal", "amountCents": 2650},
    {"description": "TAX", "kind": "tax", "amountCents": 236},
    {"description": "TOTAL", "kind": "total", "amountCents": 2886},
]


def correct_lines(table, doc_id, lines, **extra):
    fields = {"lines": lines} | extra
    return review_document(table, "user-a", doc_id, {"action": "correct", "fields": fields}, NOW)


def test_corrected_lines_that_add_up_make_the_document_ready(table):
    doc_id = processed_doc(table)

    doc = correct_lines(table, doc_id, DINNER)

    assert doc["status"] == "ready"
    assert doc["issues"] == []
    assert [line["row"] for line in doc["lines"]] == [1, 2, 3, 4, 5]  # numbered by the server
    assert (doc["totalCents"], doc["subtotalCents"], doc["taxCents"]) == (2886, 2650, 236)
    assert doc["correctedFields"] == ["lines"]


def test_corrected_lines_that_dont_add_up_say_exactly_whats_off(table):
    doc_id = processed_doc(table)
    wrong = [dict(line) for line in DINNER]
    wrong[1]["amountCents"] = 1000  # CURRY misread

    doc = correct_lines(table, doc_id, wrong)

    assert doc["status"] == "needs_review"
    assert any("subtotal" in issue for issue in doc["issues"])


def test_user_labels_are_kept_as_given_not_relabelled_by_code(table):
    """reconcile() may relabel model output; a person's correction is never second-guessed."""
    doc_id = processed_doc(table)
    lines = [
        {"description": "CASH BACK", "kind": "item", "amountCents": 2000},  # looks like payment
        {"description": "TOTAL", "kind": "total", "amountCents": 2000},
    ]

    doc = correct_lines(table, doc_id, lines)

    assert doc["lines"][0]["kind"] == "item"
    assert doc["corrections"] == []
    assert doc["status"] == "ready"


def test_a_positive_discount_amount_is_stored_as_a_reduction(table):
    doc_id = processed_doc(table)
    lines = [
        {"description": "MILK", "kind": "item", "amountCents": 500},
        {"description": "COUPON", "kind": "discount", "amountCents": 100},
        {"description": "TOTAL", "kind": "total", "amountCents": 400},
    ]

    doc = correct_lines(table, doc_id, lines)

    assert doc["lines"][1]["amountCents"] == -100
    assert doc["status"] == "ready"


@pytest.mark.parametrize(
    "lines",
    [
        [],
        "PAD THAI 14.50",
        [{"description": "X", "kind": "produce", "amountCents": 100}],
        [{"description": "X", "kind": "item", "amountCents": 1.5}],
        [{"description": "X", "kind": "item", "amountCents": True}],
        [{"description": "", "kind": "item", "amountCents": 100}],
        [{"description": "x" * 121, "kind": "item", "amountCents": 100}],
        [{"description": "X", "kind": "item", "amountCents": 10**12}],
        [{"description": "X", "kind": "item", "amountCents": 100}] * 201,
    ],
)
def test_invalid_lines_are_rejected(table, lines):
    doc_id = processed_doc(table)

    with pytest.raises(BadRequest):
        correct_lines(table, doc_id, lines)


def test_lines_and_a_separate_total_cant_be_edited_together(table):
    doc_id = processed_doc(table)

    with pytest.raises(BadRequest, match="total line"):
        correct_lines(table, doc_id, DINNER, totalCents=100)
