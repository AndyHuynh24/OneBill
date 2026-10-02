"""Handle one email from the Gmail script: dedupe → daily cap → clean → read → save → outcome.

Kept separate from the Lambda handler so it's tested directly (moto + a Bedrock Stubber), and so
SES forwarding (Phase 3) can call the same function later.
"""

import logging
from datetime import date, datetime
from typing import Any

from shared.dates import APP_TIMEZONE, app_today
from subscriptions.html_text import html_to_text, tidy_text
from subscriptions.ingest import consume_email_quota, record_use
from subscriptions.parse import read_subscription_email
from subscriptions.signals import looks_like_subscription
from subscriptions.store import claim_email, finish_email, save_subscription

MAX_BODY_CHARS = 300_000
logger = logging.getLogger(__name__)


class BadEmail(ValueError):
    """The payload isn't a usable email; the message is safe to return to the script."""


def process_email(
    table: Any, bedrock: Any, model_id: str, user_id: str, payload: dict[str, Any], now: datetime
) -> dict[str, Any]:
    email = _validated(payload, now)
    if not claim_email(table, user_id, email["messageId"], now):
        return {"status": "duplicate", "subscriptions": 0}
    record_use(table, user_id, now)
    text = tidy_text(email["text"]) if email["text"] else html_to_text(email["html"] or "")
    # Free checks first: an empty email, or one with no price and no word about recurring
    # billing, is skipped without a model call and without using the daily cap. Emails the
    # user labelled by hand are always read.
    if not text or not (email["labelled"] or looks_like_subscription(email["subject"], text)):
        finish_email(table, user_id, email["messageId"], "ignored", 0)
        return {"status": "ignored", "subscriptions": 0}
    if not consume_email_quota(table, user_id, now):
        finish_email(table, user_id, email["messageId"], "failed", 0)  # retried after the reset
        return {"status": "limit", "subscriptions": 0}
    try:
        result = read_subscription_email(
            bedrock,
            model_id,
            subject=email["subject"],
            sender=email["from"],
            received=email["received"],
            text=text,
        )
        if not result.is_subscription:
            finish_email(table, user_id, email["messageId"], "ignored", 0)
            return {"status": "ignored", "subscriptions": 0}
        for sub in result.subscriptions:
            save_subscription(table, user_id, sub, email["received"], now)
    except Exception:
        finish_email(table, user_id, email["messageId"], "failed", 0)  # may be retried
        raise
    keys = sorted({s.service_key for s in result.subscriptions})
    finish_email(table, user_id, email["messageId"], "processed", len(result.subscriptions), keys)
    return {"status": "processed", "subscriptions": len(result.subscriptions)}


def _validated(payload: Any, now: datetime) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise BadEmail("The body must be a JSON object.")
    message_id = payload.get("messageId")
    if not isinstance(message_id, str) or not 0 < len(message_id) <= 300:
        raise BadEmail("messageId is required (at most 300 characters).")
    subject, sender = payload.get("subject", ""), payload.get("from", "")
    if not isinstance(subject, str) or not isinstance(sender, str):
        raise BadEmail("subject and from must be text.")
    text, html = payload.get("text"), payload.get("html")
    if not any(isinstance(v, str) and v for v in (text, html)):
        raise BadEmail("The email needs a text or html body.")
    if any(isinstance(v, str) and len(v) > MAX_BODY_CHARS for v in (text, html)):
        raise BadEmail("The email is too large.")
    return {
        "messageId": message_id,
        "subject": subject[:500],
        "from": sender[:320],
        "text": text if isinstance(text, str) else None,
        "html": html if isinstance(html, str) else None,
        "received": _received(payload.get("date"), now),
        "labelled": payload.get("labelled") is True,
    }


def _received(value: Any, now: datetime) -> date:
    """The calendar day the email arrived, in the app's time zone (renewals are calendar days)."""
    if value is None:
        return app_today(now)
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as err:
        raise BadEmail("date must be an ISO 8601 timestamp.") from err
    if moment.tzinfo is None:
        raise BadEmail("date must include a time zone.")
    return moment.astimezone(APP_TIMEZONE).date()
