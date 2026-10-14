"""A labelled email read like a scanned receipt or bill.

Same reading and checks as a scan (the model classifies lines, code does the math, deadlines
need printed evidence), minus Textract: the email is already text. It's saved as a DOC# with
`source: "email"`, so it shows under Receipts and can be fixed and split like any scan.
"""

import hashlib
from collections.abc import Collection
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from botocore.exceptions import ClientError

from documents import store
from documents.extraction import ExtractionError, extract
from documents.textract import ExpenseResult
from documents.validate import validate
from shared import llm_cost

MAX_LINES = 2000
EMAIL_MAX_OUTPUT_TOKENS = 1500
# A bill still "processing" after this was cut off (the ingest Lambda times out at 29 s): a
# retry of the email reads it again instead of skipping it.
STALE_PROCESSING = timedelta(minutes=5)
CANT_READ = "We couldn't read this email as a bill."


def email_doc_id(message_id: str, received_ms: int) -> str:
    """Stable per email (so a retry reuses the same bill), in the usual ID shape."""
    return f"{received_ms:013d}{hashlib.sha256(message_id.encode()).hexdigest()[:8]}"


def create_email_document(
    table: Any, user_id: str, doc_id: str, subject: str, now: datetime
) -> bool:
    """A DOC# ready to be read. False if this email's bill already exists and is done or still
    being read; a failed or abandoned (stale "processing") one is taken over."""
    try:
        table.put_item(
            Item={
                "PK": f"USER#{user_id}",
                "SK": f"DOC#{doc_id}",
                "type": "DOCUMENT",
                "docId": doc_id,
                "status": "processing",
                "source": "email",
                "filename": (subject or "Email")[:120],
                "createdAt": now.isoformat(),
                "processingStartedAt": now.isoformat(),
                "updatedAt": now.isoformat(),
            },
            ConditionExpression="attribute_not_exists(SK) OR #s = :failed OR "
            "(#s = :processing AND processingStartedAt < :stale)",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":failed": "failed",
                ":processing": "processing",
                ":stale": (now - STALE_PROCESSING).isoformat(),
            },
        )
    except ClientError as err:
        if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise
    return True


def read_email_bill(
    table: Any,
    bedrock: Any,
    model_id: str,
    user_id: str,
    doc_id: str,
    text: str,
    now: datetime,
    skip_deadlines: Collection[str] = (),
) -> str:
    """Read and save the bill; returns its status (ready / needs_review / failed).
    `skip_deadlines`: kinds handled elsewhere (a renewal, when the email is also a subscription:
    the subscription owns that reminder, so there's one per renewal)."""
    expense = ExpenseResult(
        pages=1, lines=[line for line in text.splitlines() if line.strip()][:MAX_LINES]
    )
    try:
        # One short answer, no repair pass (cost review): bounds what a hostile email can cost.
        outcome = extract(
            bedrock, model_id, expense, max_output_tokens=EMAIL_MAX_OUTPUT_TOKENS, repair=False
        )
        result = validate(outcome, expense)
        if skip_deadlines:
            kept = [o for o in result.obligations if o.type not in skip_deadlines]
            result = replace(result, obligations=kept)
    except ExtractionError:
        store.mark_failed(table, user_id, doc_id, CANT_READ, now)
        return "failed"
    except Exception:
        store.mark_failed(table, user_id, doc_id, CANT_READ, now)  # retried with the email
        raise
    meta = {
        "attempts": outcome.attempts,
        "modelId": outcome.model_id,
        "promptVersion": outcome.prompt_version,
        "inputTokens": outcome.usage["inputTokens"],
        "outputTokens": outcome.usage["outputTokens"],
        **llm_cost.record(table, now, outcome.model_id, outcome.usage, outcome.model_ms),
    }
    store.save_result(table, user_id, doc_id, result, 1, meta, now)
    return result.status
