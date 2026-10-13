"""Handle one email from the Gmail script: labelled? → dedupe → clean → cap → read → save.

Kept separate from the Lambda handler so it's tested directly (moto + a Bedrock Stubber), and so
SES forwarding (Phase 3) can call the same function later.
"""

import logging
from datetime import date, datetime
from typing import Any

from documents.email_bill import create_email_document, email_doc_id, read_email_bill
from shared.dates import APP_TIMEZONE, app_today
from subscriptions.html_text import html_to_text, tidy_text
from subscriptions.ingest import consume_email_quota, consume_request_quota, record_use
from subscriptions.parse import read_subscription_email
from subscriptions.signals import PRICE, looks_like_subscription
from subscriptions.store import claim_email, finish_email, save_subscription

MAX_BODY_CHARS = 300_000
logger = logging.getLogger(__name__)


class BadEmail(ValueError):
    """The payload isn't a usable email; the message is safe to return to the script."""


def process_email(
    table: Any, bedrock: Any, model_id: str, user_id: str, payload: dict[str, Any], now: datetime
) -> dict[str, Any]:
    email = _validated(payload, now)
    # Only emails the user labelled "OneBill" are processed. Anything else is
    # ignored before it's remembered, so labelling it later still gets it read.
    if not email["labelled"]:
        return {"status": "ignored", "subscriptions": 0, "documents": 0}
    if not consume_request_quota(table, user_id, now):
        return {"status": "limit", "subscriptions": 0, "documents": 0}  # nothing stored
    if not claim_email(table, user_id, email["messageId"], now):
        return {"status": "duplicate", "subscriptions": 0, "documents": 0}
    record_use(table, user_id, now)
    text = _richest_body(email["text"], email["html"])
    if not text:
        finish_email(table, user_id, email["messageId"], "ignored", 0)
        return {"status": "ignored", "subscriptions": 0, "documents": 0}
    if not consume_email_quota(table, user_id, now):
        finish_email(table, user_id, email["messageId"], "failed", 0)  # retried after the reset
        return {"status": "limit", "subscriptions": 0, "documents": 0}

    subs = []
    try:
        # As a subscription first, when it looks like one (a free keyword check before the model)…
        if looks_like_subscription(email["subject"], text):
            result = read_subscription_email(
                bedrock,
                model_id,
                subject=email["subject"],
                sender=email["from"],
                received=email["received"],
                text=text,
            )
            if result.is_subscription:
                for sub in result.subscriptions:
                    save_subscription(table, user_id, sub, email["received"], now)
                subs = result.subscriptions
        # …and always as a bill, like a scan. A subscription owns its renewal reminder, so the
        # bill doesn't add a second one for the same date.
        doc_id = email_doc_id(email["messageId"], email["receivedMs"])
        if create_email_document(table, user_id, doc_id, email["subject"], now):
            skip = {"renewal"} if subs else set()
            read_email_bill(table, bedrock, model_id, user_id, doc_id, text, now, skip)
    except Exception:
        finish_email(table, user_id, email["messageId"], "failed", 0)  # may be retried
        raise
    keys = sorted({s.service_key for s in subs})
    finish_email(table, user_id, email["messageId"], "processed", len(subs), keys)
    return {"status": "processed", "subscriptions": len(subs), "documents": 1}


def _richest_body(text: str | None, html: str | None) -> str:
    """Many emails ship a stub plain part ("view in browser") and put the details in HTML: read
    whichever version shows more prices (then the longer one)."""
    candidates = [c for c in (tidy_text(text or ""), html_to_text(html or "")) if c]
    if not candidates:
        return ""
    return max(candidates, key=lambda c: (len(PRICE.findall(c)), len(c)))


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
        "receivedMs": _received_ms(payload.get("date"), now),
        "labelled": payload.get("labelled") is True,
    }


def _received_ms(value: Any, now: datetime) -> int:
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() * 1000)
    except (TypeError, ValueError):
        return int(now.timestamp() * 1000)


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
