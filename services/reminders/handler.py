"""Reminders Lambda: daily by EventBridge Scheduler (08:00 America/Los_Angeles), and on each new
reminder (a DynamoDB stream of OBL# inserts) to email right away when it's already due soon.

Emails aren't stored in DynamoDB (Phase 0 decision): they're looked up in Cognito at send time.
"""

import os
import re
import smtplib
import ssl
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import formataddr

import boto3
from aws_lambda_powertools import Logger, Tracer
from boto3.dynamodb.types import TypeDeserializer

from reminders.logic import roll_forward, run_reminders, send_now
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


# Reminders go out through Gmail (as the sender address) when its app password is stored as an
# encrypted SSM parameter (SecureString: free); otherwise through SES. Gmail signs its own mail,
# so it isn't held back by the SES sandbox or marked as spam for a @gmail.com sender.
SMTP_PASSWORD_PARAM = os.environ.get("SMTP_PASSWORD_PARAM")
_smtp_login: tuple[str, str] | None = None


def _gmail_login() -> tuple[str, str]:
    """(sender address, app password), read once per container. The password is never logged."""
    global _smtp_login
    if _smtp_login is None:
        param = boto3.client("ssm").get_parameter(Name=SMTP_PASSWORD_PARAM, WithDecryption=True)
        _smtp_login = (SENDER_EMAIL, param["Parameter"]["Value"].replace(" ", ""))
    return _smtp_login


def send_gmail(to: str, subject: str, text: str, html: str) -> None:
    user, password = _gmail_login()
    message = EmailMessage()
    message["From"] = formataddr(("OneBill", user))
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    with smtplib.SMTP_SSL(
        "smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=15
    ) as smtp:
        smtp.login(user, password)
        smtp.send_message(message)


def send_ses(to: str, subject: str, text: str, html: str) -> None:
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


send_email = send_gmail if SMTP_PASSWORD_PARAM else send_ses

_deserialize = TypeDeserializer().deserialize


def new_reminders(event: dict) -> dict[str, int]:
    """Stream batch: email right away for new reminders already inside the 7-day window."""
    today = app_today(datetime.now(UTC))
    counts: dict[str, int] = {}
    for record in event.get("Records", []):
        image = record.get("dynamodb", {}).get("NewImage")
        if record.get("eventName") != "INSERT" or not image:
            continue
        item = {key: _deserialize(value) for key, value in image.items()}
        if not str(item.get("SK", "")).startswith("OBL#"):
            continue
        try:
            outcome = send_now(table, item, today, find_email, send_email, APP_URL)
        except Exception as err:
            # Unclaimed again: the daily run still sends the next scheduled email. Only the
            # error type is logged: mail errors can include the recipient's address.
            logger.error("reminder_now_failed", extra={"errorType": type(err).__name__})
            outcome = "failed"
        counts[outcome] = counts.get(outcome, 0) + 1
    logger.info("reminders_now", extra=counts)
    return counts


@logger.inject_lambda_context
@tracer.capture_lambda_handler
def handler(event: dict, context: object) -> dict[str, int]:
    if "Records" in event:
        return new_reminders(event)
    now = datetime.now(UTC)
    today = app_today(now)
    # First move bills/subscriptions whose date just passed, so their next reminder exists.
    rolled = roll_forward(table, today, now)
    summary = run_reminders(table, today, find_email, send_email, APP_URL)
    logger.info("reminders_run", extra={"date": today.isoformat(), "rolled": rolled, **summary})
    return summary
