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


def test_global_daily_quota_refunds_the_user_counter(table, s3):
    users = [f"user-{i}" for i in range(logic.GLOBAL_DAILY_LIMIT // logic.USER_DAILY_LIMIT)]
    for user in users:
        for _ in range(logic.USER_DAILY_LIMIT):
            upload(table, s3, user=user)

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

    docs = list_documents(table, "user-a")

    assert [d["docId"] for d in docs] == [second, first]
    assert all(d["status"] == "pending_upload" for d in docs)


def test_get_document_returns_my_document(table, s3):
    doc_id = upload(table, s3)["docId"]

    doc = get_document(table, "user-a", doc_id)

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
