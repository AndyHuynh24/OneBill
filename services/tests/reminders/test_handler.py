import importlib
from dataclasses import dataclass

import boto3
import pytest
from botocore.stub import ANY, Stubber

POOL = "us-east-1_TESTPOOL1"
SENDER = "sender@example.com"
USER = "6f1c2a9e-1111-2222-3333-444455556666"


@dataclass
class FakeLambdaContext:
    function_name: str = "reminders"
    memory_limit_in_mb: int = 256
    invoked_function_arn: str = "arn:aws:lambda:us-east-1:123456789012:function:reminders"
    aws_request_id: str = "req-1"


@pytest.fixture
def env(table, monkeypatch):
    monkeypatch.setenv("USER_POOL_ID", POOL)
    monkeypatch.setenv("SENDER_EMAIL", SENDER)
    monkeypatch.setenv("APP_URL", "http://localhost:5173")
    import reminders.handler as handler

    handler = importlib.reload(handler)
    cognito = boto3.client("cognito-idp", region_name="us-east-1")
    ses = boto3.client("sesv2", region_name="us-east-1")
    monkeypatch.setattr(handler, "cognito", cognito)
    monkeypatch.setattr(handler, "ses", ses)
    with Stubber(cognito) as c_stub, Stubber(ses) as s_stub:
        yield {"handler": handler, "table": table, "cognito": c_stub, "ses": s_stub}
        c_stub.assert_no_pending_responses()
        s_stub.assert_no_pending_responses()


def cognito_user(email, verified="true"):
    return {
        "Users": [
            {
                "Username": USER,
                "Attributes": [
                    {"Name": "sub", "Value": USER},
                    {"Name": "email", "Value": email},
                    {"Name": "email_verified", "Value": verified},
                ],
            }
        ]
    }


def test_find_email_filters_by_sub(env):
    env["cognito"].add_response(
        "list_users",
        cognito_user("andy@example.com"),
        {"UserPoolId": POOL, "Filter": f'sub = "{USER}"', "Limit": 1},
    )

    assert env["handler"].find_email(USER) == "andy@example.com"


def test_unverified_email_is_not_used(env):
    env["cognito"].add_response(
        "list_users",
        cognito_user("andy@example.com", verified="false"),
        {"UserPoolId": POOL, "Filter": ANY, "Limit": 1},
    )

    assert env["handler"].find_email(USER) is None


def test_unexpected_user_id_never_reaches_the_filter(env):
    assert env["handler"].find_email('x" or email ^= "') is None  # no Cognito call queued


def test_send_email_uses_the_verified_sender_and_both_bodies(env):
    env["ses"].add_response(
        "send_email",
        {"MessageId": "m-1"},
        {
            "FromEmailAddress": SENDER,
            "Destination": {"ToAddresses": ["andy@example.com"]},
            "Content": {
                "Simple": {
                    "Subject": {"Data": "OneBill: 1 thing due soon", "Charset": "UTF-8"},
                    "Body": {
                        "Text": {"Data": "text", "Charset": "UTF-8"},
                        "Html": {"Data": "<p>html</p>", "Charset": "UTF-8"},
                    },
                }
            },
        },
    )

    env["handler"].send_email(
        "andy@example.com", "OneBill: 1 thing due soon", "text", "<p>html</p>"
    )


def test_handler_with_nothing_due_sends_nothing(env):
    summary = env["handler"].handler({}, FakeLambdaContext())

    assert summary["emails"] == 0
