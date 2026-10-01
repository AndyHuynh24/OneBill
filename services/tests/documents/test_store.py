from datetime import UTC, date, datetime

import pytest

from documents import store
from documents.validate import Obligation, ValidationResult

NOW = datetime(2026, 9, 30, 18, 0, tzinfo=UTC)
USER = "6f1c2a9e-1111-2222-3333-444455556666"
DOC = "1790000000000aaaaaaaa"


def pending(table, status="pending_upload"):
    table.put_item(
        Item={
            "PK": f"USER#{USER}",
            "SK": f"DOC#{DOC}",
            "type": "DOCUMENT",
            "docId": DOC,
            "status": status,
            "s3Key": f"uploads/{USER}/{DOC}",
            "contentType": "image/jpeg",
        }
    )


def doc_item(table):
    return table.get_item(Key={"PK": f"USER#{USER}", "SK": f"DOC#{DOC}"})["Item"]


def result(status="ready", obligations=()):
    return ValidationResult(
        status=status,
        issues=[]
        if status == "ready"
        else ["Items add up to $9.00 but the receipt's subtotal is $12.00."],
        doc_type="receipt",
        vendor="TARGET",
        document_date="2026-09-28",
        currency="USD",
        total_cents=2618,
        subtotal_cents=2448,
        tax_cents=170,
        tip_cents=0,
        fees_cents=0,
        discounts_cents=-200,
        lines=[{"row": 1, "description": "BANANAS", "kind": "item", "amountCents": 129}],
        corrections=[],
        obligations=list(obligations),
    )


RETURN = Obligation("return_by", date(2026, 10, 28), "RETURNS ACCEPTED WITHIN 30 DAYS", 2618)


# --- keys ---------------------------------------------------------------------------------------


def test_parse_upload_key():
    assert store.parse_upload_key(f"uploads/{USER}/{DOC}") == (USER, DOC)


@pytest.mark.parametrize(
    "key",
    [
        "uploads/u",
        "other/u/1790000000000aaaaaaaa",
        f"uploads/{USER}/not-an-id",
        "uploads//x",
        "uploads/a b/x",
    ],
)
def test_parse_upload_key_rejects_unexpected_keys(key):
    assert store.parse_upload_key(key) is None


# --- state machine ------------------------------------------------------------------------------


def test_claim_moves_pending_to_processing_once(table):
    pending(table)

    first = store.claim(table, USER, DOC, NOW)
    second = store.claim(table, USER, DOC, NOW)

    assert first is not None
    assert first["contentType"] == "image/jpeg"
    assert second is None  # duplicate event: nothing to do
    assert doc_item(table)["status"] == "processing"


def test_claim_records_the_s3_version_that_triggered_it(table):
    pending(table)

    store.claim(table, USER, DOC, NOW, version_id="v-123")

    assert doc_item(table)["s3VersionId"] == "v-123"


def test_claim_unknown_document_returns_none(table):
    assert store.claim(table, USER, DOC, NOW) is None


def test_record_textract_job(table):
    pending(table)
    store.claim(table, USER, DOC, NOW)

    store.record_textract_job(table, USER, DOC, "job-123", NOW)

    assert doc_item(table)["textractJobId"] == "job-123"


def test_save_result_writes_document_and_obligations_atomically(table):
    pending(table)
    store.claim(table, USER, DOC, NOW)

    store.save_result(
        table, USER, DOC, result(obligations=[RETURN]), pages=1, meta={"attempts": 1}, now=NOW
    )

    item = doc_item(table)
    assert item["status"] == "ready"
    assert item["vendor"] == "TARGET"
    assert item["totalCents"] == 2618
    assert item["attempts"] == 1
    assert len(item["obligationKeys"]) == 1

    obligations = table.query(
        IndexName="GSI1",
        KeyConditionExpression="GSI1PK = :d",
        ExpressionAttributeValues={":d": "DUE#2026-10-28"},
    )["Items"]
    assert len(obligations) == 1
    obl = obligations[0]
    assert obl["PK"] == f"USER#{USER}"
    assert obl["SK"].startswith("OBL#2026-10-28#")
    assert obl["GSI1SK"].startswith(f"USER#{USER}#OBL#")
    assert obl["kind"] == "return_by"
    assert obl["docId"] == DOC
    assert obl["vendor"] == "TARGET"
    assert obl["remindersSent"] == []


def test_save_result_only_applies_to_a_processing_document(table):
    pending(table, status="ready")

    with pytest.raises(store.StateConflict):
        store.save_result(table, USER, DOC, result(), pages=1, meta={}, now=NOW)


def test_needs_review_keeps_issues(table):
    pending(table)
    store.claim(table, USER, DOC, NOW)

    store.save_result(table, USER, DOC, result(status="needs_review"), pages=1, meta={}, now=NOW)

    item = doc_item(table)
    assert item["status"] == "needs_review"
    assert item["issues"][0].startswith("Items add up to")


def test_mark_failed_records_a_friendly_reason(table):
    pending(table)
    store.claim(table, USER, DOC, NOW)

    store.mark_failed(table, USER, DOC, "We couldn't read this document.", NOW)

    item = doc_item(table)
    assert item["status"] == "failed"
    assert item["failureReason"] == "We couldn't read this document."
