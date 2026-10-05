"""One incoming email, end to end: labelled? → dedupe → clean → cap → read → save → outcome.

Only emails the user labelled "OneBill" are processed (2026-10-03). Each is read like a scanned
bill, and also as a subscription when it looks like one.
"""

from datetime import UTC, datetime

import boto3
import pytest
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from botocore.stub import ANY, Stubber

from documents.extraction import TOOL_NAME as RECEIPT_TOOL
from subscriptions import parse
from subscriptions.ingest import DAILY_EMAIL_LIMIT, consume_email_quota
from subscriptions.process import BadEmail, process_email
from subscriptions.store import list_subscriptions

MODEL = "m"
NOW = datetime(2026, 10, 1, 19, 0, tzinfo=UTC)
EMAIL = {
    "messageId": "18c2f0a9d3b4e5f6",
    "subject": "Your Netflix receipt",
    "from": "Netflix <info@account.netflix.com>",
    "date": "2026-10-01T15:04:05.000Z",
    "html": "<p>Standard plan $15.49</p><p>Total $15.49</p>"
    "<p>Next billing date | November 1, 2026</p>",
    "labelled": True,
}
NETFLIX = {
    "service": "Netflix",
    "plan": None,
    "amount": "$15.49",
    "currency": "USD",
    "billing_cycle": "monthly",
    "status": "charged",
    "next_renewal": "2026-11-01",
    "evidence": {"amount": "$15.49", "next_renewal": "November 1, 2026"},
}
NETFLIX_BILL = {
    "doc_type": "receipt",
    "vendor": "Netflix",
    "document_date": "2026-10-01",
    "currency": "USD",
    "lines": [
        {"description": "Standard plan", "amount": "15.49", "kind": "item"},
        {"description": "Total", "amount": "15.49", "kind": "total"},
    ],
    "deadlines": [],
}
BILL_EMAIL = EMAIL | {
    "messageId": "pge-oct",
    "subject": "Your PG&E bill is ready",
    "from": "PG&E <billing@pge.com>",
    "html": "<p>Electric charges 72.10</p><p>Gas charges 12.00</p><p>Amount due $84.10</p>"
    "<p>Due by 10/20/2026</p>",
}
BILL = {
    "doc_type": "bill",
    "vendor": "PG&E",
    "document_date": "2026-10-01",
    "currency": "USD",
    "lines": [
        {"description": "Electric charges", "amount": "72.10", "kind": "item"},
        {"description": "Gas charges", "amount": "12.00", "kind": "item"},
        {"description": "Amount due", "amount": "84.10", "kind": "balance_due"},
    ],
    "deadlines": [
        {
            "type": "payment_due",
            "date": "2026-10-20",
            "days_after_purchase": None,
            "evidence": "Due by 10/20/2026",
        }
    ],
}


def answer(payload, tool=parse.TOOL_NAME):
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"toolUse": {"toolUseId": "t", "name": tool, "input": payload}}],
            }
        },
        "stopReason": "tool_use",
        "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
        "metrics": {"latencyMs": 1},
    }


@pytest.fixture
def bedrock():
    client = boto3.client("bedrock-runtime", region_name="us-east-1")
    with Stubber(client) as stub:
        yield client, stub
        stub.assert_no_pending_responses()


PARAMS = {
    "modelId": MODEL,
    "system": ANY,
    "messages": ANY,
    "toolConfig": ANY,
    "inferenceConfig": ANY,
}


def expect(stub, payload):
    """The subscription reader's answer."""
    stub.add_response("converse", answer(payload), PARAMS)


def expect_receipt(stub, payload):
    """The bill reader's answer (the same reading as a scan)."""
    stub.add_response("converse", answer(payload, RECEIPT_TOOL), PARAMS)


def expect_netflix(stub, subscriptions=(NETFLIX,), bill=NETFLIX_BILL):
    """Read as a subscription first, then as a bill."""
    expect(
        stub, {"is_subscription_email": bool(subscriptions), "subscriptions": list(subscriptions)}
    )
    expect_receipt(stub, bill)


def documents(table):
    return table.query(
        KeyConditionExpression=Key("PK").eq("USER#user-a") & Key("SK").begins_with("DOC#")
    )["Items"]


def sent_requests(client):
    sent = []
    client.meta.events.register(
        "before-parameter-build.bedrock-runtime.Converse", lambda params, **_: sent.append(params)
    )
    return sent


# --- only labelled emails ------------------------------------------------------------------------


def test_an_unlabelled_email_is_ignored_without_the_model_the_cap_or_a_memory(table, bedrock):
    client, _ = bedrock  # nothing queued: a model call would fail the test

    result = process_email(table, client, MODEL, "user-a", EMAIL | {"labelled": False}, NOW)

    assert result == {"status": "ignored", "subscriptions": 0, "documents": 0}
    usage = table.get_item(Key={"PK": "USER#user-a", "SK": "USAGE#2026-10-01"}).get("Item")
    assert usage is None  # the daily cap wasn't touched
    marker = table.get_item(Key={"PK": "USER#user-a", "SK": f"EMAIL#{EMAIL['messageId']}"})
    assert "Item" not in marker  # not remembered...


def test_an_email_labelled_after_it_was_first_sent_is_read(table, bedrock):
    """...so labelling it later works: the script sends it again, now labelled."""
    client, stub = bedrock
    process_email(table, client, MODEL, "user-a", EMAIL | {"labelled": False}, NOW)
    expect_netflix(stub)

    result = process_email(table, client, MODEL, "user-a", EMAIL, NOW)

    assert result == {"status": "processed", "subscriptions": 1, "documents": 1}


def test_a_payload_without_the_labelled_flag_counts_as_unlabelled(table, bedrock):
    client, _ = bedrock
    old_script = {k: v for k, v in EMAIL.items() if k != "labelled"}

    assert process_email(table, client, MODEL, "user-a", old_script, NOW)["status"] == "ignored"


# --- a labelled email is a bill, and a subscription when it looks like one ----------------------


def test_a_labelled_subscription_email_is_a_bill_and_a_subscription(table, bedrock):
    """Andy: 'the label can be subscription too'."""
    client, stub = bedrock
    expect_netflix(stub)

    result = process_email(table, client, MODEL, "user-a", EMAIL, NOW)

    assert result == {"status": "processed", "subscriptions": 1, "documents": 1}
    assert documents(table)[0]["vendor"] == "Netflix"
    (sub,) = list_subscriptions(table, "user-a")
    assert (sub["service"], sub["amountCents"], sub["nextRenewal"]) == (
        "Netflix",
        1549,
        "2026-11-01",
    )


def test_a_labelled_bill_is_only_a_bill(table, bedrock):
    client, stub = bedrock
    expect_receipt(stub, BILL)

    result = process_email(table, client, MODEL, "user-a", BILL_EMAIL, NOW)

    assert result["documents"] == 1
    (doc,) = documents(table)
    assert (doc["status"], doc["source"], doc["vendor"]) == ("ready", "email", "PG&E")
    assert int(doc["totalCents"]) == 8410
    assert [o["kind"] for o in doc["obligations"]] == ["payment_due"]  # due date → reminder
    assert list_subscriptions(table, "user-a") == []  # not subscription-like: no second read


def test_when_the_subscription_reader_says_no_its_still_a_bill(table, bedrock):
    client, stub = bedrock
    expect_netflix(stub, subscriptions=())

    result = process_email(table, client, MODEL, "user-a", EMAIL | {"messageId": "promo1"}, NOW)

    assert result == {"status": "processed", "subscriptions": 0, "documents": 1}
    assert list_subscriptions(table, "user-a") == []


def test_sending_the_same_email_twice_reads_it_once(table, bedrock):
    client, stub = bedrock
    expect_netflix(stub)
    process_email(table, client, MODEL, "user-a", EMAIL, NOW)

    result = process_email(table, client, MODEL, "user-a", EMAIL, NOW)  # nothing queued: no call

    assert result["status"] == "duplicate"


def test_a_model_failure_is_recorded_so_the_email_can_be_retried(table, bedrock):
    client, stub = bedrock
    stub.add_client_error(
        "converse", service_error_code="ThrottlingException", http_status_code=429
    )

    with pytest.raises(ClientError, match="Throttling"):
        process_email(table, client, MODEL, "user-a", EMAIL, NOW)

    expect_netflix(stub)
    assert process_email(table, client, MODEL, "user-a", EMAIL, NOW)["status"] == "processed"
    assert len(documents(table)) == 1  # the retry reuses the same bill


def test_over_the_daily_cap_nothing_is_read(table, bedrock):
    client, _ = bedrock
    for _ in range(DAILY_EMAIL_LIMIT):
        consume_email_quota(table, "user-a", NOW)

    result = process_email(table, client, MODEL, "user-a", EMAIL, NOW)

    assert result["status"] == "limit"


def test_an_empty_email_is_ignored_without_a_model_call(table, bedrock):
    client, _ = bedrock

    result = process_email(
        table, client, MODEL, "user-a", EMAIL | {"html": "<style>x</style>"}, NOW
    )

    assert result["status"] == "ignored"


@pytest.mark.parametrize(
    "overrides",
    [
        {"messageId": ""},
        {"messageId": "x" * 301},
        {"subject": 5},
        {"html": None, "text": None},
        {"html": "x" * 300_001},
        {"date": "yesterday"},
    ],
)
def test_bad_payloads_are_rejected(table, bedrock, overrides):
    client, _ = bedrock

    with pytest.raises(BadEmail):
        process_email(table, client, MODEL, "user-a", EMAIL | overrides, NOW)


def test_the_received_date_is_the_local_calendar_day(table, bedrock):
    """10:30 pm in Los Angeles on Sep 30 is already Oct 1 in UTC."""
    client, stub = bedrock
    expect_netflix(stub)
    sent = sent_requests(client)

    process_email(table, client, MODEL, "user-a", EMAIL | {"date": "2026-10-01T05:30:00Z"}, NOW)

    subscription_read = sent[0]["messages"][0]["content"][0]["text"]
    assert "Received: 2026-09-30" in subscription_read


def test_a_stub_plain_text_part_doesnt_hide_the_prices_in_the_html(table, bedrock):
    """Emails often ship a one-line plain part ("view in browser"): read the richer version."""
    client, stub = bedrock
    expect_netflix(stub)
    sent = sent_requests(client)
    email = EMAIL | {"messageId": "stub1", "text": "View this email in your browser."}

    process_email(table, client, MODEL, "user-a", email, NOW)

    assert all("$15.49" in r["messages"][0]["content"][0]["text"] for r in sent)


RENEWAL = {
    "type": "renewal",
    "date": "2026-11-01",
    "days_after_purchase": None,
    "evidence": "Next billing date | November 1, 2026",
}


def test_a_subscriptions_renewal_gets_one_reminder_not_a_second_from_its_bill(table, bedrock):
    """Tello: the subscription and its bill both saw "Next billing date": one reminder only."""
    client, stub = bedrock
    expect_netflix(stub, bill=NETFLIX_BILL | {"doc_type": "bill", "deadlines": [RENEWAL]})

    process_email(table, client, MODEL, "user-a", EMAIL, NOW)

    assert documents(table)[0].get("obligations", []) == []
    reminders = table.query(
        KeyConditionExpression=Key("PK").eq("USER#user-a") & Key("SK").begins_with("OBL#")
    )["Items"]
    assert len(reminders) == 1  # the subscription's


def test_a_bill_that_isnt_a_subscription_keeps_its_own_renewal_reminder(table, bedrock):
    client, stub = bedrock
    expect_netflix(
        stub, subscriptions=(), bill=NETFLIX_BILL | {"doc_type": "bill", "deadlines": [RENEWAL]}
    )

    process_email(table, client, MODEL, "user-a", EMAIL | {"messageId": "not-sub"}, NOW)

    assert [o["kind"] for o in documents(table)[0]["obligations"]] == ["renewal"]


# --- security review fixes ------------------------------------------------------------------------

from datetime import timedelta  # noqa: E402

from documents.email_bill import create_email_document  # noqa: E402
from subscriptions.ingest import DAILY_REQUEST_LIMIT, consume_request_quota  # noqa: E402


def test_a_bill_left_processing_by_a_timeout_is_read_again_on_retry(table):
    assert create_email_document(table, "user-a", "1790000000000aaaaaaaa", "Bill", NOW)
    assert not create_email_document(table, "user-a", "1790000000000aaaaaaaa", "Bill", NOW)

    later = NOW + timedelta(minutes=6)

    assert create_email_document(table, "user-a", "1790000000000aaaaaaaa", "Bill", later)


def test_over_the_request_cap_nothing_is_stored(table, bedrock):
    client, _ = bedrock
    for _ in range(DAILY_REQUEST_LIMIT):
        consume_request_quota(table, "user-a", NOW)

    result = process_email(table, client, MODEL, "user-a", EMAIL, NOW)

    assert result["status"] == "limit"
    marker = table.get_item(Key={"PK": "USER#user-a", "SK": f"EMAIL#{EMAIL['messageId']}"})
    assert "Item" not in marker
