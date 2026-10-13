import base64
import json
from datetime import UTC, date, datetime

import boto3
import pytest

from api.documents import logic
from api.documents.logic import (
    BadRequest,
    NotFound,
    QuotaExceeded,
    create_upload,
    get_document,
    list_documents,
    list_obligations,
    parse_groups,
)

BUCKET = "onebill-docs-test"
NOW = datetime(2026, 9, 30, 18, 0, 0, tzinfo=UTC)
UPLOADER = set()  # anyone signed in may upload
OWNER = {"owners"}


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


def test_anyone_signed_in_may_upload_three_a_day(table, s3):
    for _ in range(3):
        upload(table, s3, groups=set())

    with pytest.raises(QuotaExceeded, match="today's 3 uploads"):
        upload(table, s3, groups=set())


def test_the_owner_has_25_a_day(table, s3):
    for _ in range(25):
        upload(table, s3, user="andy", groups=OWNER)
    with pytest.raises(QuotaExceeded, match="today's 25 uploads"):
        upload(table, s3, user="andy", groups=OWNER)


def test_owner_uploads_are_marked_so_reading_skips_the_shared_page_budget(table, s3):
    doc_id = upload(table, s3, user="andy", groups=OWNER)["docId"]
    item = table.get_item(Key={"PK": "USER#andy", "SK": f"DOC#{doc_id}"})["Item"]
    assert item["owner"] is True

    other = upload(table, s3)["docId"]
    assert "owner" not in table.get_item(Key={"PK": "USER#user-a", "SK": f"DOC#{other}"})["Item"]


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


def test_upload_limits_decided_2026_10_12():
    limits = (
        logic.USER_DAILY_LIMIT,
        logic.OWNER_DAILY_LIMIT,
        logic.RENEWALS_PER_MONTH,
        logic.MONTHLY_APP_LIMIT,
    )
    assert limits == (3, 25, 2, 200)


def test_there_is_no_shared_daily_limit_only_a_count(table, s3):
    # 10 users × 3 scans: more than the old shared 15 a day, and nobody is refused.
    for i in range(10 * logic.USER_DAILY_LIMIT):
        upload(table, s3, user=f"user-{i // logic.USER_DAILY_LIMIT}")
    upload(table, s3, user="andy", groups=OWNER)

    daily = table.get_item(Key={"PK": "GLOBAL", "SK": "USAGE#2026-09-30"})["Item"]
    assert daily["uploads"] == 31  # the owner's scans are counted too


def test_the_whole_app_has_a_monthly_cap_that_counts_the_owner_too(table, s3):
    # The month is nearly used up: the next scan (even the owner's) is refused, and the daily
    # counters it took are given back.
    table.put_item(Item={"PK": "GLOBAL", "SK": "USAGE#2026-09", "uploads": logic.MONTHLY_APP_LIMIT})

    with pytest.raises(QuotaExceeded, match="this month"):
        upload(table, s3)

    usage = table.get_item(Key={"PK": "USER#user-a", "SK": "USAGE#2026-09-30"})["Item"]
    assert usage["uploads"] == 0
    assert "Item" not in table.get_item(Key={"PK": "GLOBAL", "SK": "USAGE#2026-09-30"})


def test_monthly_counter_outlives_the_month(table, s3):
    upload(table, s3)

    month = table.get_item(Key={"PK": "GLOBAL", "SK": "USAGE#2026-09"})["Item"]
    assert month["uploads"] == 1
    assert month["expiresAt"] > int(NOW.timestamp()) + 31 * 24 * 3600


def test_usage_counters_expire(table, s3):
    upload(table, s3)

    usage = table.get_item(Key={"PK": "USER#user-a", "SK": "USAGE#2026-09-30"})["Item"]
    assert usage["expiresAt"] > int(NOW.timestamp())


# --- Usage and renewals --------------------------------------------------------------------------


def use_up_today(table, s3, user="user-a", when=NOW):
    for _ in range(logic.USER_DAILY_LIMIT):
        create_upload(table, s3, BUCKET, user, UPLOADER, "r.jpg", "image/jpeg", 1000, when)


def test_usage_starts_with_three_scans_and_two_renewals(table):
    assert logic.get_usage(table, "user-a", UPLOADER, NOW) == {
        "dailyLimit": 3,
        "usedToday": 0,
        "leftToday": 3,
        "renewalsLeft": 2,
    }


def test_usage_counts_todays_scans(table, s3):
    upload(table, s3)

    usage = logic.get_usage(table, "user-a", UPLOADER, NOW)
    assert (usage["usedToday"], usage["leftToday"]) == (1, 2)


def test_owner_usage_has_25_a_day_and_no_renewals(table):
    usage = logic.get_usage(table, "andy", OWNER, NOW)
    assert (usage["dailyLimit"], usage["renewalsLeft"]) == (25, 0)


def test_renewing_gives_three_more_scans_today(table, s3):
    use_up_today(table, s3)

    usage = logic.renew_usage(table, "user-a", UPLOADER, NOW)

    assert (usage["leftToday"], usage["renewalsLeft"]) == (3, 1)
    for _ in range(3):
        upload(table, s3)  # allowed again
    with pytest.raises(QuotaExceeded):
        upload(table, s3)


def test_cant_renew_while_scans_are_left(table, s3):
    upload(table, s3)

    with pytest.raises(logic.Conflict, match="still have scans"):
        logic.renew_usage(table, "user-a", UPLOADER, NOW)
    assert logic.get_usage(table, "user-a", UPLOADER, NOW)["renewalsLeft"] == 2


def test_cant_renew_before_scanning_at_all(table):
    with pytest.raises(logic.Conflict, match="still have scans"):
        logic.renew_usage(table, "user-a", UPLOADER, NOW)


def test_only_two_renewals_a_month_even_across_days(table, s3):
    later = NOW.replace(day=29)
    for when in (later, NOW):  # two different days, same month
        use_up_today(table, s3, when=when)
        logic.renew_usage(table, "user-a", UPLOADER, when)
    use_up_today(table, s3)

    with pytest.raises(logic.Conflict, match="this month's 2 renewals"):
        logic.renew_usage(table, "user-a", UPLOADER, NOW)
    assert logic.get_usage(table, "user-a", UPLOADER, NOW)["leftToday"] == 0


def test_renewals_come_back_in_a_new_month(table, s3):
    for _ in range(2):
        use_up_today(table, s3)
        logic.renew_usage(table, "user-a", UPLOADER, NOW)

    october = datetime(2026, 10, 1, 18, 0, tzinfo=UTC)
    assert logic.get_usage(table, "user-a", UPLOADER, october)["renewalsLeft"] == 2
    month = table.get_item(Key={"PK": "USER#user-a", "SK": "USAGE#2026-09"})["Item"]
    assert month["expiresAt"] > int(NOW.timestamp()) + 31 * 24 * 3600


def test_the_owner_cant_renew(table):
    with pytest.raises(logic.Conflict):
        logic.renew_usage(table, "andy", OWNER, NOW)


def test_renewals_are_one_users_only(table, s3):
    use_up_today(table, s3, user="user-a")
    logic.renew_usage(table, "user-a", UPLOADER, NOW)

    assert logic.get_usage(table, "user-b", UPLOADER, NOW)["renewalsLeft"] == 2


def test_app_usage_lists_the_last_7_days_newest_first(table):
    for day, scans in (("2026-09-30", 4), ("2026-09-28", 2), ("2026-09-24", 1), ("2026-09-23", 9)):
        table.put_item(Item={"PK": "GLOBAL", "SK": f"USAGE#{day}", "uploads": scans})
    table.put_item(Item={"PK": "GLOBAL", "SK": "USAGE#2026-09", "uploads": 99})  # not a day

    days = logic.app_usage(table, NOW)["days"]

    assert [d["date"] for d in days] == [
        "2026-09-30",
        "2026-09-29",
        "2026-09-28",
        "2026-09-27",
        "2026-09-26",
        "2026-09-25",
        "2026-09-24",
    ]  # 8 days ago (09-23) is left out; quiet days show as 0
    assert [d["scans"] for d in days] == [4, 0, 2, 0, 0, 0, 1]
    assert days[0] == {"date": "2026-09-30", "scans": 4, "pages": 0, "emails": 0, "renewals": 0}


def test_a_refund_after_a_renewal_never_goes_below_zero(table):
    table.put_item(Item={"PK": "USER#user-a", "SK": "USAGE#2026-09-30", "uploads": 0})

    logic._refund_quota(table, "USER#user-a", "2026-09-30")

    item = table.get_item(Key={"PK": "USER#user-a", "SK": "USAGE#2026-09-30"})["Item"]
    assert item["uploads"] == 0


def test_a_failed_app_count_doesnt_fail_the_upload(table, s3, monkeypatch):
    from botocore.exceptions import ClientError

    real = table.update_item

    def flaky(**kwargs):
        if kwargs["Key"] == {"PK": "GLOBAL", "SK": "USAGE#2026-09-30"}:
            raise ClientError({"Error": {"Code": "TransactionConflictException"}}, "UpdateItem")
        return real(**kwargs)

    monkeypatch.setattr(table, "update_item", flaky)

    assert upload(table, s3)["docId"]  # the scan still goes through


def test_renewing_leaves_the_shared_counts_out_of_the_transaction(table, s3):
    use_up_today(table, s3)
    logic.renew_usage(table, "user-a", UPLOADER, NOW)

    month = table.get_item(Key={"PK": "GLOBAL", "SK": "USAGE#2026-09"})["Item"]
    assert month["renewals"] == 1


def test_app_usage_totals_today_and_this_month(table, s3):
    use_up_today(table, s3)
    logic.renew_usage(table, "user-a", UPLOADER, NOW)
    upload(table, s3, user="andy", groups=OWNER)
    table.update_item(
        Key={"PK": "GLOBAL", "SK": "USAGE#2026-09"},
        UpdateExpression="ADD pages :p, emails :e",
        ExpressionAttributeValues={":p": 12, ":e": 5},
    )

    usage = logic.app_usage(table, NOW)

    assert usage["today"] == {"scans": 4, "pages": 0, "emails": 0, "renewals": 1}
    assert usage["month"] == {"scans": 4, "pages": 12, "emails": 5, "renewals": 1}
    assert usage["limits"] == {"scans": 200, "pages": 600, "emails": 300}


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
            "kind": "payment_due",
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
        (["owners", "admins"], {"owners", "admins"}),
        ("[owners admins]", {"owners", "admins"}),  # how HTTP API flattens array claims
        ("owners", {"owners"}),
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
                "kind": "payment_due",
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
            "fields": {"deadlines": [{"kind": "payment_due", "dueDate": "2026-11-15"}]},
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
        {
            "action": "correct",
            "fields": {"deadlines": [{"kind": "payment_due", "dueDate": "soon"}]},
        },
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
        "fields": {"deadlines": [{"kind": "payment_due", "dueDate": "2026-11-15"}]},
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


def test_daily_limits_can_be_switched_off_for_testing(table, s3):
    """Deploy-time switch (-c dailyLimits=off); the default deploy turns them back on."""
    for _ in range(logic.USER_DAILY_LIMIT + 2):
        create_upload(
            table,
            s3,
            BUCKET,
            "user-a",
            UPLOADER,
            "r.jpg",
            "image/jpeg",
            1000,
            NOW,
            enforce_limits=False,
        )


# --- removing a reminder, deleting a document --------------------------------------------------

from api.documents.logic import delete_document, remove_obligation  # noqa: E402


def test_removing_a_deadline_takes_it_off_the_document_too(table):
    doc_id = processed_doc(table, obligations=("2026-10-28", "2027-09-28"))
    keep, remove = sorted(o["SK"] for o in obligations_for(table))
    table.update_item(
        Key={"PK": "USER#user-a", "SK": f"DOC#{doc_id}"},
        UpdateExpression="SET obligations = :o",
        ExpressionAttributeValues={
            ":o": [
                {"kind": "payment_due", "dueDate": "2026-10-28", "evidence": "a"},
                {"kind": "payment_due", "dueDate": "2027-09-28", "evidence": "b"},
            ]
        },
    )

    remove_obligation(table, "user-a", remove.rsplit("#", 1)[1], NOW)

    assert [o["SK"] for o in obligations_for(table)] == [keep]
    doc = get_document(table, "user-a", doc_id)
    assert doc["obligations"] == [{"kind": "payment_due", "dueDate": "2026-10-28", "evidence": "a"}]


def test_removing_a_subscription_reminder_turns_its_reminders_off(table):
    from subscriptions.parse import ParsedSubscription
    from subscriptions.store import list_subscriptions, save_subscription

    sub = ParsedSubscription(
        service="Netflix",
        service_key="netflix",
        plan=None,
        amount_cents=1549,
        currency="USD",
        cycle="monthly",
        status="charged",
        charge_date=date(2026, 10, 1),
        period_end=None,
        next_renewal=date(2026, 11, 1),
    )
    save_subscription(table, "user-a", sub, date(2026, 10, 1), NOW)
    (reminder,) = obligations_for(table)

    remove_obligation(table, "user-a", reminder["oblId"], NOW)

    assert obligations_for(table) == []
    assert list_subscriptions(table, "user-a")[0]["remindersOff"] is True


def test_someone_elses_reminder_is_not_found(table):
    processed_doc(table, user="user-b")
    (other,) = obligations_for(table, user="user-b")

    with pytest.raises(NotFound):
        remove_obligation(table, "user-a", other["oblId"], NOW)
    with pytest.raises(NotFound):
        remove_obligation(table, "user-a", "../PROFILE", NOW)


def test_deleting_a_document_removes_it_its_deadlines_and_its_photo(table, s3):
    upload(table, s3)  # a pending upload with an S3 key
    doc_id = processed_doc(table)
    key = f"uploads/user-a/{doc_id}"
    table.update_item(
        Key={"PK": "USER#user-a", "SK": f"DOC#{doc_id}"},
        UpdateExpression="SET s3Key = :k",
        ExpressionAttributeValues={":k": key},
    )
    s3.put_object(Bucket=BUCKET, Key=key, Body=b"jpeg")

    delete_document(table, s3, BUCKET, "user-a", doc_id, NOW)

    with pytest.raises(NotFound):
        get_document(table, "user-a", doc_id)
    assert obligations_for(table) == []
    assert s3.list_objects_v2(Bucket=BUCKET, Prefix=key).get("KeyCount", 0) == 0


def test_a_document_still_being_read_cant_be_deleted_yet(table, s3):
    doc_id = processed_doc(table, status="processing")
    table.update_item(
        Key={"PK": "USER#user-a", "SK": f"DOC#{doc_id}"},
        UpdateExpression="SET processingStartedAt = :t",
        ExpressionAttributeValues={":t": NOW.isoformat()},
    )

    with pytest.raises(BadRequest, match="still reading"):
        delete_document(table, s3, BUCKET, "user-a", doc_id, NOW)


def test_someone_elses_document_cant_be_deleted(table, s3):
    doc_id = processed_doc(table, user="user-b")

    with pytest.raises(NotFound):
        delete_document(table, s3, BUCKET, "user-a", doc_id, NOW)


# --- "Looks right" on a flagged line (phase 2d) --------------------------------------------------


def flagged_doc(table, status="ready"):
    doc_id = processed_doc(table, status=status, obligations=())
    table.update_item(
        Key={"PK": "USER#user-a", "SK": f"DOC#{doc_id}"},
        UpdateExpression="SET confidence = :c",
        ExpressionAttributeValues={
            ":c": {
                "level": "check",
                "checks": [
                    {"row": 1, "reason": "Hard to read on the photo"},
                    {"row": 4, "reason": "Hard to read on the photo"},
                ],
            }
        },
    )
    return doc_id


def test_confirming_a_flagged_line_clears_just_that_flag(table):
    doc_id = flagged_doc(table)

    doc = review_document(table, "user-a", doc_id, {"action": "confirm", "rows": [1]}, now=NOW)

    assert doc["confidence"]["checks"] == [{"row": 4, "reason": "Hard to read on the photo"}]
    assert doc["confidence"]["level"] == "check"


def test_confirming_the_last_flag_marks_the_bill_checked_by_you(table):
    doc_id = flagged_doc(table)
    review_document(table, "user-a", doc_id, {"action": "confirm", "rows": [1]}, now=NOW)

    doc = review_document(table, "user-a", doc_id, {"action": "confirm", "rows": [4]}, now=NOW)

    assert doc["confidence"] == {"level": "confirmed", "checks": []}
    assert doc["status"] == "ready"  # confirming never changes the status


def test_a_bill_whose_totals_dont_add_up_still_asks_for_a_check_after_confirming(table):
    doc_id = flagged_doc(table, status="needs_review")

    doc = review_document(table, "user-a", doc_id, {"action": "confirm", "rows": [1, 4]}, now=NOW)

    assert doc["confidence"] == {"level": "check", "checks": []}
    assert doc["status"] == "needs_review"


@pytest.mark.parametrize("rows", [None, [], "1", [1.5], [True], list(range(201))])
def test_confirm_needs_a_list_of_row_numbers(table, rows):
    doc_id = flagged_doc(table)

    with pytest.raises(BadRequest):
        review_document(table, "user-a", doc_id, {"action": "confirm", "rows": rows}, now=NOW)


def test_accepting_or_fixing_a_bill_clears_its_flags(table):
    doc_id = flagged_doc(table)

    doc = review_document(table, "user-a", doc_id, {"action": "accept"}, now=NOW)

    assert doc["confidence"] == {"level": "confirmed", "checks": []}


def test_the_receipts_list_includes_each_bills_confidence(table):
    flagged_doc(table)

    (summary,) = list_documents(table, "user-a", now=NOW)

    assert summary["confidence"]["level"] == "check"


def test_fixing_a_line_on_the_bill_rechecks_the_math_and_keeps_other_flags(table):
    doc_id = flagged_doc(table)
    stored = [
        {"row": 1, "description": "BANANAS", "kind": "item", "amountCents": 129},
        {"row": 2, "description": "MILK", "kind": "item", "amountCents": 419},
        {"row": 3, "description": "SUBTOTAL", "kind": "subtotal", "amountCents": 548},
        {"row": 4, "description": "TAX", "kind": "tax", "amountCents": 54},  # misread 0.44
        {"row": 5, "description": "TOTAL", "kind": "total", "amountCents": 592},
    ]
    table.update_item(
        Key={"PK": "USER#user-a", "SK": f"DOC#{doc_id}"},
        UpdateExpression="SET #l = :l",
        ExpressionAttributeNames={"#l": "lines"},
        ExpressionAttributeValues={":l": stored},
    )
    edited = [{k: v for k, v in line.items() if k != "row"} for line in stored]
    edited[3]["amountCents"] = 44  # the user fixes the tax: it adds up now

    doc = review_document(
        table, "user-a", doc_id, {"action": "correct", "fields": {"lines": edited}}, now=NOW
    )

    assert doc["status"] == "ready"
    # Row 1 wasn't touched, so its "hard to read" flag stays for the user to tick.
    assert doc["confidence"] == {
        "level": "check",
        "checks": [{"row": 1, "reason": "Hard to read on the photo"}],
    }


def test_confirming_on_a_deleted_bill_doesnt_bring_it_back(table):
    doc_id = flagged_doc(table)
    current = get_document(table, "user-a", doc_id)
    table.delete_item(Key={"PK": "USER#user-a", "SK": f"DOC#{doc_id}"})

    with pytest.raises(BadRequest):
        logic._confirm_lines(table, "user-a", doc_id, current, [1], NOW)

    assert "Item" not in table.get_item(Key={"PK": "USER#user-a", "SK": f"DOC#{doc_id}"})
