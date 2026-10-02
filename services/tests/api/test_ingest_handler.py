"""POST /ingest/email: no Cognito sign-in; the ingest key alone decides whose email this is."""

import importlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

import boto3
import pytest
from botocore.stub import ANY, Stubber

from subscriptions import parse
from subscriptions.ingest import create_key

EMAIL = {
    "messageId": "18c2f0a9d3b4e5f6",
    "subject": "Your Netflix receipt",
    "from": "info@account.netflix.com",
    "date": "2026-10-01T15:04:05Z",
    "text": "Amount: $15.49\nNext billing date: November 1, 2026",
}


@dataclass
class Ctx:
    function_name: str = "ingest"
    memory_limit_in_mb: int = 256
    invoked_function_arn: str = "arn:aws:lambda:us-east-1:123456789012:function:ingest"
    aws_request_id: str = "req-1"


def event(body, authorization=None, raw=None):
    headers = {"content-type": "application/json"}
    if authorization is not None:
        headers["authorization"] = authorization
    return {
        "version": "2.0",
        "routeKey": "POST /ingest/email",
        "rawPath": "/ingest/email",
        "rawQueryString": "",
        "headers": headers,
        "body": raw if raw is not None else json.dumps(body),
        "isBase64Encoded": False,
        "requestContext": {
            "accountId": "123456789012",
            "apiId": "api",
            "http": {
                "method": "POST",
                "path": "/ingest/email",
                "protocol": "HTTP/1.1",
                "sourceIp": "1.2.3.4",
            },
            "requestId": "req-1",
            "routeKey": "POST /ingest/email",
            "stage": "$default",
        },
    }


@pytest.fixture
def ingest(table, monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "m")
    import api.ingest.handler as handler

    handler = importlib.reload(handler)
    client = boto3.client("bedrock-runtime", region_name="us-east-1")
    monkeypatch.setattr(handler, "bedrock", client)
    with Stubber(client) as stub:
        yield handler, stub
        stub.assert_no_pending_responses()


def call(handler, evt):
    response = handler.handler(evt, Ctx())
    return response["statusCode"], json.loads(response["body"] or "{}")


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


def test_a_valid_key_processes_the_email_for_its_owner(table, ingest):
    handler, stub = ingest
    key = create_key(table, "user-a", {"uploaders"}, datetime(2026, 10, 1, tzinfo=UTC))
    sub = {
        "service": "Netflix",
        "amount": "$15.49",
        "status": "charged",
        "billing_cycle": "monthly",
        "next_renewal": "2026-11-01",
        "evidence": {"amount": "$15.49", "next_renewal": "November 1, 2026"},
    }
    stub.add_response(
        "converse",
        answer({"is_subscription_email": True, "subscriptions": [sub]}),
        {"modelId": "m", "system": ANY, "messages": ANY, "toolConfig": ANY, "inferenceConfig": ANY},
    )

    status, body = call(handler, event(EMAIL | {"userId": "user-b"}, f"Bearer {key}"))

    assert (status, body) == (200, {"status": "processed", "subscriptions": 1})
    assert table.get_item(Key={"PK": "USER#user-a", "SK": "SUB#netflix"}).get("Item")
    assert not table.get_item(Key={"PK": "USER#user-b", "SK": "SUB#netflix"}).get("Item")


@pytest.mark.parametrize("authorization", [None, "", "Bearer otk_" + "x" * 43, "Bearer nope"])
def test_without_a_valid_key_nothing_happens(table, ingest, authorization):
    handler, _ = ingest  # no Bedrock call queued: any call would fail the test

    status, _ = call(handler, event(EMAIL, authorization))

    assert status == 401


def test_a_bad_payload_is_400(table, ingest):
    handler, _ = ingest
    key = create_key(table, "user-a", {"uploaders"}, datetime(2026, 10, 1, tzinfo=UTC))

    assert call(handler, event(EMAIL | {"messageId": ""}, f"Bearer {key}"))[0] == 400
    assert call(handler, event(None, f"Bearer {key}", raw="not json"))[0] == 400


def test_an_oversized_request_is_413_before_any_work(table, ingest):
    handler, _ = ingest

    status, _ = call(handler, event(None, "Bearer whatever", raw="x" * 600_000))

    assert status == 413


def test_over_the_daily_cap_is_429_so_the_script_retries_later(table, ingest, monkeypatch):
    handler, _ = ingest
    key = create_key(table, "user-a", {"uploaders"}, datetime(2026, 10, 1, tzinfo=UTC))
    monkeypatch.setattr("subscriptions.process.consume_email_quota", lambda *_: False)

    status, body = call(handler, event(EMAIL, f"Bearer {key}"))

    assert status == 429
    assert body["status"] == "limit"
