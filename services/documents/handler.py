"""Extract Lambda: S3 "Object Created" (via EventBridge) and Textract completion (via SNS).

Upload → claim the DOC → (photo) Textract sync | (PDF) checks + Textract async → SNS →
parse → Bedrock extraction → reconcile → validate → save DOC + OBL#.
Errors before the claim propagate (EventBridge retries, then the DLQ). After the claim, the
document is marked failed with a friendly reason instead, so the user sees what happened.
"""

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import boto3
from aws_lambda_powertools import Logger, Tracer
from botocore.config import Config
from botocore.exceptions import ClientError

from documents import store
from documents.extraction import ExtractionError, extract
from documents.pdf import count_pages, is_pdf
from documents.textract import analyze_sync, fetch_async, parse_expense, start_async
from documents.validate import validate

logger = Logger()
tracer = Tracer()

table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
s3 = boto3.client("s3")
textract = boto3.client("textract")
bedrock = boto3.client(
    "bedrock-runtime", config=Config(read_timeout=60, retries={"mode": "adaptive"})
)

BUCKET = os.environ["BUCKET_NAME"]
MODEL_ID = os.environ["BEDROCK_MODEL_ID"]
TOPIC_ARN = os.environ["TEXTRACT_TOPIC_ARN"]
ROLE_ARN = os.environ["TEXTRACT_ROLE_ARN"]
MAX_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 10

UNREADABLE = "We couldn't read this document. Try a clearer, well-lit photo."
GENERIC_FAILURE = "Something went wrong while reading this document. Please upload it again."
TEXTRACT_REJECTIONS = frozenset(
    {
        "UnsupportedDocumentException",
        "BadDocumentException",
        "InvalidS3ObjectException",
        "DocumentTooLargeException",
    }
)


class Refused(Exception):
    """A file we deliberately don't process; the message is shown to the user."""


@logger.inject_lambda_context
@tracer.capture_lambda_handler
def handler(event: dict, context: object) -> None:
    if event.get("source") == "aws.s3":
        _on_upload(event["detail"])
    elif event.get("Records") and event["Records"][0].get("EventSource") == "aws:sns":
        for record in event["Records"]:
            _on_textract_done(json.loads(record["Sns"]["Message"]))
    else:
        logger.warning("unrecognized_event", extra={"keys": sorted(event)})


def _on_upload(detail: dict[str, Any]) -> None:
    parsed = store.parse_upload_key(detail["object"]["key"])
    if parsed is None:
        logger.warning("unexpected_key")
        return
    user_id, doc_id = parsed
    item = store.claim(table, user_id, doc_id, _now())
    if item is None:
        logger.info("already_claimed", extra={"docId": doc_id})
        return
    with _fail_on_error(user_id, doc_id):
        if int(detail["object"].get("size", 0)) > MAX_BYTES:
            raise Refused("Files can be at most 10 MB.")
        key = item["s3Key"]
        if item.get("contentType") == "application/pdf":
            data = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
            if not is_pdf(data):
                raise Refused("This file isn't a valid PDF.")
            if count_pages(data) > MAX_PDF_PAGES:
                raise Refused(f"PDFs can have at most {MAX_PDF_PAGES} pages.")
            job_id = start_async(textract, BUCKET, key, doc_id, TOPIC_ARN, ROLE_ARN)
            store.record_textract_job(table, user_id, doc_id, job_id, _now())
            logger.info("textract_async_started", extra={"docId": doc_id})
            return
        _process(user_id, doc_id, analyze_sync(textract, BUCKET, key))


def _on_textract_done(message: dict[str, Any]) -> None:
    parsed = store.parse_upload_key(message.get("DocumentLocation", {}).get("S3ObjectName", ""))
    if parsed is None:
        logger.warning("unexpected_key")
        return
    user_id, doc_id = parsed
    with _fail_on_error(user_id, doc_id):
        if message.get("Status") != "SUCCEEDED":
            raise Refused(UNREADABLE)
        _process(user_id, doc_id, fetch_async(textract, message["JobId"]))


@tracer.capture_method
def _process(user_id: str, doc_id: str, responses: list[dict[str, Any]]) -> None:
    expense = parse_expense(responses)
    outcome = extract(bedrock, MODEL_ID, expense)
    result = validate(outcome, expense)
    meta = {
        "attempts": outcome.attempts,
        "modelId": outcome.model_id,
        "promptVersion": outcome.prompt_version,
        "inputTokens": outcome.usage["inputTokens"],
        "outputTokens": outcome.usage["outputTokens"],
    }
    store.save_result(table, user_id, doc_id, result, expense.pages, meta, _now())
    # Log outcomes and counts only: never document text or extracted fields.
    logger.info(
        "document_processed",
        extra={
            "docId": doc_id,
            "status": result.status,
            "issues": len(result.issues),
            "corrections": len(result.corrections),
            "obligations": len(result.obligations),
            "pages": expense.pages,
            "redactions": outcome.redactions,
            **meta,
        },
    )


@contextmanager
def _fail_on_error(user_id: str, doc_id: str) -> Iterator[None]:
    """After a document is claimed, turn errors into a 'failed' status with a friendly reason."""
    try:
        yield
    except Refused as exc:
        reason = str(exc)
        logger.info("document_refused", extra={"docId": doc_id, "reason": reason})
    except ExtractionError as exc:
        reason = UNREADABLE
        logger.warning("extraction_failed", extra={"docId": doc_id, "error": str(exc)})
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in TEXTRACT_REJECTIONS:
            reason = UNREADABLE
            logger.warning("textract_rejected", extra={"docId": doc_id, "code": code})
        else:
            reason = GENERIC_FAILURE
            logger.exception("processing_error", extra={"docId": doc_id})
    except Exception:
        reason = GENERIC_FAILURE
        logger.exception("processing_error", extra={"docId": doc_id})
    else:
        return
    store.mark_failed(table, user_id, doc_id, reason, _now())


def _now() -> datetime:
    return datetime.now(UTC)
