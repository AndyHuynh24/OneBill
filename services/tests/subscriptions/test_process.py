"""One incoming email, end to end: cap → dedupe → clean → read → save → outcome."""

from datetime import UTC, datetime

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.stub import ANY, Stubber

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
    "html": "<p>Amount | $15.49</p><p>Next billing date | November 1, 2026</p>",
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


def answer(payload):
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {"toolUse": {"toolUseId": "t", "name": parse.TOOL_NAME, "input": payload}}
                ],
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


def expect(stub, payload):
    stub.add_response(
        "converse",
        answer(payload),
        {
            "modelId": MODEL,
            "system": ANY,
            "messages": ANY,
            "toolConfig": ANY,
            "inferenceConfig": ANY,
        },
    )


def test_a_subscription_email_is_read_and_saved(table, bedrock):
    client, stub = bedrock
    expect(stub, {"is_subscription_email": True, "subscriptions": [NETFLIX]})

    result = process_email(table, client, MODEL, "user-a", EMAIL, NOW)

    assert result == {"status": "processed", "subscriptions": 1, "documents": 0}
    (sub,) = list_subscriptions(table, "user-a")
    assert (sub["service"], sub["amountCents"], sub["nextRenewal"]) == (
        "Netflix",
        1549,
        "2026-11-01",
    )


def test_sending_the_same_email_twice_costs_one_model_call(table, bedrock):
    client, stub = bedrock
    expect(stub, {"is_subscription_email": True, "subscriptions": [NETFLIX]})
    process_email(table, client, MODEL, "user-a", EMAIL, NOW)

    result = process_email(table, client, MODEL, "user-a", EMAIL, NOW)  # nothing queued: no call

    assert result["status"] == "duplicate"


def test_a_promo_is_ignored(table, bedrock):
    client, stub = bedrock
    expect(stub, {"is_subscription_email": False, "subscriptions": []})

    result = process_email(table, client, MODEL, "user-a", EMAIL | {"messageId": "promo1"}, NOW)

    assert result == {"status": "ignored", "subscriptions": 0, "documents": 0}
    assert list_subscriptions(table, "user-a") == []


def test_a_model_failure_is_recorded_so_the_email_can_be_retried(table, bedrock):
    client, stub = bedrock
    stub.add_client_error(
        "converse", service_error_code="ThrottlingException", http_status_code=429
    )

    with pytest.raises(ClientError, match="Throttling"):
        process_email(table, client, MODEL, "user-a", EMAIL, NOW)

    expect(stub, {"is_subscription_email": True, "subscriptions": [NETFLIX]})
    assert process_email(table, client, MODEL, "user-a", EMAIL, NOW)["status"] == "processed"


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
    expect(stub, {"is_subscription_email": True, "subscriptions": [NETFLIX]})
    sent = []
    client.meta.events.register(
        "before-parameter-build.bedrock-runtime.Converse", lambda params, **_: sent.append(params)
    )

    process_email(table, client, MODEL, "user-a", EMAIL | {"date": "2026-10-01T05:30:00Z"}, NOW)

    assert "Received: 2026-09-30" in sent[0]["messages"][0]["content"][0]["text"]


def test_an_obvious_non_subscription_is_skipped_without_the_model_or_the_cap(table, bedrock):
    client, _ = bedrock  # nothing queued: a model call would fail the test
    order = EMAIL | {
        "messageId": "order1",
        "subject": "Your order shipped",
        "html": "<p>Total $45.20</p>",
    }

    result = process_email(table, client, MODEL, "user-a", order, NOW)

    assert result == {"status": "ignored", "subscriptions": 0, "documents": 0}
    usage = table.get_item(Key={"PK": "USER#user-a", "SK": "USAGE#2026-10-01"}).get("Item")
    assert usage is None  # the daily cap wasn't touched


def test_a_stub_plain_text_part_doesnt_hide_the_prices_in_the_html(table, bedrock):
    """Emails often ship a one-line plain part ("view in browser"): read the richer version."""
    client, stub = bedrock
    sent = []
    client.meta.events.register(
        "before-parameter-build.bedrock-runtime.Converse", lambda params, **_: sent.append(params)
    )
    expect(stub, {"is_subscription_email": True, "subscriptions": [NETFLIX]})
    email = EMAIL | {
        "messageId": "stub1",
        "text": "View this email in your browser.",
        "html": EMAIL["html"],
    }

    process_email(table, client, MODEL, "user-a", email, NOW)

    assert "$15.49" in sent[0]["messages"][0]["content"][0]["text"]


# --- labelled emails are read like a scanned receipt or bill (decision A) ----------------------

from boto3.dynamodb.conditions import Key  # noqa: E402

from documents.extraction import TOOL_NAME as RECEIPT_TOOL  # noqa: E402

BILL_EMAIL = EMAIL | {
    "messageId": "pge-oct",
    "subject": "Your PG&E bill is ready",
    "from": "PG&E <billing@pge.com>",
    "html": "<p>Electric charges 72.10</p><p>Gas charges 12.00</p><p>Amount due $84.10</p>"
    "<p>Due by 10/20/2026</p>",
    "labelled": True,
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


def expect_receipt(stub, payload):
    response = answer(payload)
    response["output"]["message"]["content"][0]["toolUse"]["name"] = RECEIPT_TOOL
    stub.add_response(
        "converse",
        response,
        {
            "modelId": MODEL,
            "system": ANY,
            "messages": ANY,
            "toolConfig": ANY,
            "inferenceConfig": ANY,
        },
    )


def documents(table):
    return table.query(
        KeyConditionExpression=Key("PK").eq("USER#user-a") & Key("SK").begins_with("DOC#")
    )["Items"]


def test_a_labelled_email_becomes_a_bill_like_a_scan(table, bedrock):
    client, stub = bedrock
    expect_receipt(stub, BILL)

    result = process_email(table, client, MODEL, "user-a", BILL_EMAIL, NOW)

    assert result["documents"] == 1
    (doc,) = documents(table)
    assert (doc["status"], doc["source"], doc["vendor"]) == ("ready", "email", "PG&E")
    assert int(doc["totalCents"]) == 8410
    assert [o["kind"] for o in doc["obligations"]] == ["payment_due"]  # due date → reminder
    assert list_subscriptions(table, "user-a") == []  # not a subscription: no second read


def test_a_labelled_subscription_email_is_a_bill_and_a_subscription(table, bedrock):
    """Andy: 'the label can be subscription too'."""
    client, stub = bedrock
    netflix_bill = {
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
    expect_receipt(stub, netflix_bill)
    expect(stub, {"is_subscription_email": True, "subscriptions": [NETFLIX]})
    email = EMAIL | {
        "messageId": "nflx-label",
        "html": "<p>Standard plan $15.49</p><p>Total $15.49</p>"
        "<p>Next billing date | November 1, 2026</p>",
        "labelled": True,
    }

    result = process_email(table, client, MODEL, "user-a", email, NOW)

    assert (result["documents"], result["subscriptions"]) == (1, 1)
    assert documents(table)[0]["vendor"] == "Netflix"
    assert list_subscriptions(table, "user-a")[0]["service"] == "Netflix"


def test_a_retried_labelled_email_doesnt_make_a_second_bill(table, bedrock):
    client, stub = bedrock
    stub.add_client_error(
        "converse", service_error_code="ThrottlingException", http_status_code=429
    )
    with pytest.raises(ClientError):
        process_email(table, client, MODEL, "user-a", BILL_EMAIL, NOW)

    expect_receipt(stub, BILL)
    process_email(table, client, MODEL, "user-a", BILL_EMAIL, NOW)

    (doc,) = documents(table)
    assert doc["status"] == "ready"
