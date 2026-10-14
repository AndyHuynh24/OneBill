"""Extract Lambda: S3 "Object Created" (via EventBridge) and Textract completion (via SNS).

Upload → claim the DOC → (photo) Textract sync | (PDF) checks + Textract async → SNS →
parse → Bedrock extraction → reconcile → validate → save DOC + OBL#.
Errors before the claim propagate (EventBridge retries, then the DLQ). After the claim, the
document is marked failed with a friendly reason instead, so the user sees what happened.
"""

import json
import os
import time
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
from documents.textract import (
    ExpenseResult,
    analyze_sync,
    fetch_async,
    parse_expense,
    start_async,
)
from documents.validate import validate
from shared import llm_cost

logger = Logger()
tracer = Tracer()

table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
s3 = boto3.client("s3")
textract = boto3.client("textract")
# Worst case 2 model calls × 2 attempts × 45 s stays well inside the 300 s Lambda timeout, so a
# slow call fails the document instead of leaving it stuck in "processing".
bedrock = boto3.client(
    "bedrock-runtime",
    config=Config(read_timeout=45, retries={"mode": "adaptive", "max_attempts": 2}),
)

BUCKET = os.environ["BUCKET_NAME"]
MODEL_ID = os.environ["BEDROCK_MODEL_ID"]
TOPIC_ARN = os.environ["TEXTRACT_TOPIC_ARN"]
ROLE_ARN = os.environ["TEXTRACT_ROLE_ARN"]
# Photo tiles next to the OCR text. Off until the eval shows it helps; "off" rolls back.
PHOTO_TILES = os.environ.get("PHOTO_TILES", "off") == "on"
MAX_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 10

UNREADABLE = "We couldn't read this document. Try a clearer, well-lit photo."
TOO_MANY_PAGES = f"PDFs can have at most {MAX_PDF_PAGES} pages."
UNKNOWN_PAGES = "We couldn't check how many pages this PDF has. Try a photo instead."
MONTHLY_LIMIT = "OneBill has reached this month's reading limit. Please upload it again on the 1st."
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
        _on_upload(event["detail"], event.get("time"))
    elif event.get("Records") and event["Records"][0].get("EventSource") == "aws:sns":
        for record in event["Records"]:
            _on_textract_done(json.loads(record["Sns"]["Message"]))
    else:
        logger.warning("unrecognized_event", extra={"keys": sorted(event)})


def _on_upload(detail: dict[str, Any], event_time: str | None = None) -> None:
    started = time.monotonic()
    obj = detail["object"]
    parsed = store.parse_upload_key(obj["key"])
    if parsed is None:
        logger.warning("unexpected_key")
        return
    user_id, doc_id = parsed
    version = obj.get("version-id")
    item = store.claim(table, user_id, doc_id, _now(), version)
    if item is None:
        logger.info("already_claimed", extra={"docId": doc_id})
        return
    timings = _since("queue", event_time) | _since("sinceRequest", item.get("createdAt"))
    reading_later = False  # a PDF read by an async Textract job: keep it until that finishes
    try:
        with _fail_on_error(user_id, doc_id):
            reading_later = _read_upload(user_id, doc_id, obj, item, version, timings, started)
    finally:
        if not reading_later:
            _forget_upload(item)


def _read_upload(
    user_id: str,
    doc_id: str,
    obj: dict[str, Any],
    item: dict[str, Any],
    version: str | None,
    timings: dict[str, Any],
    started: float,
) -> bool:
    """Read a claimed upload. True when an async Textract job will finish reading it later."""
    if int(obj.get("size", 0)) > MAX_BYTES:
        raise Refused("Files can be at most 10 MB.")
    key = item["s3Key"]
    if item.get("contentType") == "application/pdf":
        kwargs = {"VersionId": version} if version else {}
        data = s3.get_object(Bucket=BUCKET, Key=key, **kwargs)["Body"].read()
        if not is_pdf(data):
            raise Refused("This file isn't a valid PDF.")
        pages = count_pages(data)
        if pages is None:
            raise Refused(UNKNOWN_PAGES)
        if pages > MAX_PDF_PAGES:
            raise Refused(TOO_MANY_PAGES)
        _reserve_pages(item, pages)
        job_id = start_async(textract, BUCKET, key, doc_id, TOPIC_ARN, ROLE_ARN, version)
        store.record_textract_job(table, user_id, doc_id, job_id, _now())
        logger.info("textract_async_started", extra={"docId": doc_id, "pages": pages})
        return True
    _reserve_pages(item, 1)
    ocr_started = time.monotonic()
    expense = parse_expense(analyze_sync(textract, BUCKET, key, version))
    timings["textract"] = _ms(ocr_started)
    images = _photo_images(key, version, expense) if PHOTO_TILES else []
    _process(user_id, doc_id, expense, timings, started, images)
    return False


def _on_textract_done(message: dict[str, Any]) -> None:
    parsed = store.parse_upload_key(message.get("DocumentLocation", {}).get("S3ObjectName", ""))
    if parsed is None:
        logger.warning("unexpected_key")
        return
    user_id, doc_id = parsed
    # Only act on the job this document is waiting for: ignores duplicate SNS deliveries
    # (which would otherwise pay for Bedrock twice) and mismatched or forged messages.
    item = store.get(table, user_id, doc_id)
    expected = (
        item is not None
        and item.get("status") == "processing"
        and item.get("textractJobId") == message.get("JobId")
        and message.get("JobTag") == doc_id
    )
    if not expected:
        logger.warning("unexpected_textract_message", extra={"docId": doc_id})
        return
    started = time.monotonic()
    timings = _since("textract", item.get("updatedAt"))  # async job: started → completion
    try:
        with _fail_on_error(user_id, doc_id):
            if message.get("Status") != "SUCCEEDED":
                raise Refused(UNREADABLE)
            expense = parse_expense(fetch_async(textract, message["JobId"]))
            if expense.pages > MAX_PDF_PAGES:
                raise Refused(TOO_MANY_PAGES)  # Textract is paid; don't also pay for Bedrock
            _process(user_id, doc_id, expense, timings, started)
    finally:
        _forget_upload(item)


def _forget_upload(item: dict[str, Any]) -> None:
    """Receipt photos and PDFs aren't kept: once read (or refused), the exact uploaded version
    is deleted, so no copy lingers in the versioned bucket. A 1-day lifecycle rule on uploads/
    catches anything a failed delete leaves behind."""
    key = item.get("s3Key")
    if not key:
        return
    version = item.get("s3VersionId")
    try:
        s3.delete_object(Bucket=BUCKET, Key=key, **({"VersionId": version} if version else {}))
    except Exception:  # noqa: BLE001 (the lifecycle rule removes it within a day anyway)
        logger.warning("upload_delete_failed", extra={"docId": item.get("docId")})


def _photo_images(key: str, version: str | None, expense: ExpenseResult) -> list[bytes]:
    """The photo as model-ready tiles (PII blacked out). Any problem means text only: the image
    is a layout hint, never a reason to fail a scan."""
    try:
        from documents.image_render import model_images  # needs Pillow (the images layer)

        kwargs = {"VersionId": version} if version else {}
        data = s3.get_object(Bucket=BUCKET, Key=key, **kwargs)["Body"].read()
        return model_images(data, expense)
    except Exception:  # noqa: BLE001 (deliberately broad: fall back to text only)
        logger.warning("photo_images_skipped", exc_info=True)
        return []


@tracer.capture_method
def _process(
    user_id: str,
    doc_id: str,
    expense: ExpenseResult,
    timings: dict[str, Any],
    started: float,
    images: list[bytes] | None = None,
) -> None:
    outcome = extract(bedrock, MODEL_ID, expense, images or [])
    result = validate(outcome, expense)
    now = _now()
    meta = {
        "attempts": outcome.attempts,
        "modelId": outcome.model_id,
        "promptVersion": outcome.prompt_version,
        "inputTokens": outcome.usage["inputTokens"],
        "outputTokens": outcome.usage["outputTokens"],
        **llm_cost.record(
            table, now, outcome.model_id, outcome.usage, outcome.model_ms, expense.pages
        ),
    }
    save_started = time.monotonic()
    store.save_result(table, user_id, doc_id, result, expense.pages, meta, now)
    timings |= {"model": outcome.model_ms, "save": _ms(save_started), "handler": _ms(started)}
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
            "images": len(images or []),
            "redactions": outcome.redactions,
            "timingsMs": timings,
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
    except ExtractionError:
        reason = UNREADABLE
        # The message can quote model output (document content): log the type only.
        logger.warning("extraction_failed", extra={"docId": doc_id, "errorType": "ExtractionError"})
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


def _limits_on() -> bool:
    """The monthly OCR page budget, unless switched off for testing (-c dailyLimits=off)."""
    return os.environ.get("DAILY_LIMITS", "on") != "off"


def _reserve_pages(item: dict[str, Any], pages: int) -> None:
    """Take pages from the shared monthly budget; the owner's are counted but never refused."""
    if _limits_on() and not item.get("owner"):
        if not store.consume_page_budget(table, pages, _now()):
            raise Refused(MONTHLY_LIMIT)
    else:
        store.count_pages(table, pages, _now())


def _ms(since: float) -> int:
    return round((time.monotonic() - since) * 1000)


def _since(name: str, timestamp: str | None) -> dict[str, int]:
    """Milliseconds from an ISO timestamp (an event time, an item's createdAt) until now."""
    if not timestamp:
        return {}
    try:
        then = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return {}
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    return {name: max(0, round((_now() - then).total_seconds() * 1000))}


def _now() -> datetime:
    return datetime.now(UTC)
