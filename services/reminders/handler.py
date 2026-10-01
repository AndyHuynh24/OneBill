"""Reminders Lambda, run daily by EventBridge Scheduler (08:00 America/Los_Angeles).

Emails aren't stored in DynamoDB (Phase 0 decision): they're looked up in Cognito at send time.
"""

import os
import re
from datetime import UTC, datetime

import boto3
from aws_lambda_powertools import Logger, Tracer

from reminders.logic import run_reminders
from shared.dates import app_today

logger = Logger()
tracer = Tracer()

table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
cognito = boto3.client("cognito-idp")
ses = boto3.client("sesv2")

USER_POOL_ID = os.environ["USER_POOL_ID"]
SENDER_EMAIL = os.environ["SENDER_EMAIL"]
APP_URL = os.environ["APP_URL"]
_SUB = re.compile(r"^[A-Za-z0-9-]{1,128}$")


def find_email(user_id: str) -> str | None:
    """The user's email if Cognito has verified it; otherwise None (no reminder)."""
    if not _SUB.match(user_id):
        return None  # never interpolate unexpected characters into the filter
    response = cognito.list_users(UserPoolId=USER_POOL_ID, Filter=f'sub = "{user_id}"', Limit=1)
    users = response["Users"]
    if not users:
        return None
    attrs = {a["Name"]: a["Value"] for a in users[0].get("Attributes", [])}
    return attrs.get("email") if attrs.get("email_verified") == "true" else None


def send_email(to: str, subject: str, text: str, html: str) -> None:
    ses.send_email(
        FromEmailAddress=SENDER_EMAIL,
        Destination={"ToAddresses": [to]},
        Content={
            "Simple": {
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {
                    "Text": {"Data": text, "Charset": "UTF-8"},
                    "Html": {"Data": html, "Charset": "UTF-8"},
                },
            }
        },
    )


@logger.inject_lambda_context
@tracer.capture_lambda_handler
def handler(event: dict, context: object) -> dict[str, int]:
    today = app_today(datetime.now(UTC))
    summary = run_reminders(table, today, find_email, send_email, APP_URL)
    logger.info("reminders_run", extra={"date": today.isoformat(), **summary})  # counts only
    return summary
