"""Documents API Lambda: documents, obligations, splits, subscriptions, and the Gmail ingest key.

Thin: read the verified user ID and groups from the JWT claims, validate the request shape,
call logic, map errors to HTTP status codes.
"""

import os
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import unquote

import boto3
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.event_handler import APIGatewayHttpResolver, Response, content_types
from aws_lambda_powertools.event_handler.exceptions import UnauthorizedError
from aws_lambda_powertools.logging import correlation_paths
from botocore.config import Config

from api.documents import logic, splits
from shared.dates import app_today, parse_iso_date
from subscriptions import ingest as ingest_keys
from subscriptions import store as subscriptions

logger = Logger()
tracer = Tracer()
app = APIGatewayHttpResolver()

table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
s3 = boto3.client("s3", config=Config(signature_version="s3v4"))
BUCKET = os.environ["BUCKET_NAME"]
DEFAULT_OBLIGATION_WINDOW_DAYS = 30


def _claims() -> dict[str, Any]:
    return app.current_event.request_context.authorizer.jwt_claim


def _user_id() -> str:
    # The JWT authorizer verified the token; `sub` is the only trusted identity.
    user_id = _claims().get("sub")
    if not user_id:
        raise UnauthorizedError("Unauthorized")
    return user_id


@app.exception_handler(logic.DocumentsError)
def _documents_error(err: logic.DocumentsError) -> Response:
    return Response(
        status_code=err.status,
        content_type=content_types.APPLICATION_JSON,
        body={"message": err.message},
    )


@app.post("/documents")
@tracer.capture_method
def create_document() -> tuple[dict[str, Any], int]:
    user_id = _user_id()
    body = app.current_event.json_body or {}
    filename, content_type, size = (
        body.get("filename"),
        body.get("contentType"),
        body.get("sizeBytes"),
    )
    if not isinstance(filename, str) or not isinstance(content_type, str):
        raise logic.BadRequest("filename and contentType are required.")
    if not isinstance(size, int) or isinstance(size, bool):
        raise logic.BadRequest("sizeBytes must be an integer.")
    groups = logic.parse_groups(_claims().get("cognito:groups"))
    result = logic.create_upload(
        table,
        s3,
        BUCKET,
        user_id,
        groups,
        filename,
        content_type,
        size,
        now=datetime.now(UTC),
        # Temporary testing switch, set only by `cdk deploy -c dailyLimits=off`.
        enforce_limits=os.environ.get("DAILY_LIMITS", "on") != "off",
    )
    logger.info("upload_created", extra={"docId": result["docId"]})
    return result, 201


@app.get("/documents")
@tracer.capture_method
def list_documents() -> dict[str, Any]:
    documents = logic.list_documents(table, _user_id())
    return {"documents": documents, "review": logic.review_stats(documents)}


@app.get("/documents/<doc_id>")
@tracer.capture_method
def get_document(doc_id: str) -> dict[str, Any]:
    return logic.get_document(table, _user_id(), doc_id)


@app.patch("/documents/<doc_id>")
@tracer.capture_method
def review_document(doc_id: str) -> dict[str, Any]:
    body = app.current_event.json_body or {}
    if not isinstance(body, dict):
        raise logic.BadRequest("Body must be a JSON object.")
    document = logic.review_document(table, _user_id(), doc_id, body, now=datetime.now(UTC))
    logger.info(
        "document_reviewed", extra={"docId": doc_id, "outcome": document.get("reviewOutcome")}
    )
    return document


@app.get("/obligations")
@tracer.capture_method
def list_obligations() -> dict[str, Any]:
    user_id = _user_id()
    event = app.current_event
    raw_days = event.get_query_string_value("withinDays", str(DEFAULT_OBLIGATION_WINDOW_DAYS))
    if not re.fullmatch(r"\d{1,3}", raw_days):  # isdigit() would accept "²" and crash int()
        raise logic.BadRequest("withinDays must be a whole number.")
    # The browser may pass its own local date; otherwise use the app time zone.
    today = parse_iso_date(event.get_query_string_value("from")) or app_today(datetime.now(UTC))
    return {"obligations": logic.list_obligations(table, user_id, today, int(raw_days))}


@app.put("/documents/<doc_id>/split")
@tracer.capture_method
def save_split(doc_id: str) -> dict[str, Any]:
    body = app.current_event.json_body or {}
    if not isinstance(body, dict):
        raise logic.BadRequest("Body must be a JSON object.")
    split = splits.save_split(table, _user_id(), doc_id, body, now=datetime.now(UTC))
    # Counts only: names stay out of logs.
    logger.info(
        "split_saved",
        extra={"docId": doc_id, "people": len(split["members"]), "version": split["version"]},
    )
    return split


@app.get("/subscriptions")
@tracer.capture_method
def list_subscriptions() -> dict[str, Any]:
    user_id = _user_id()
    return {
        "subscriptions": subscriptions.list_subscriptions(table, user_id),
        "connection": ingest_keys.key_status(table, user_id),
    }


@app.patch("/subscriptions/<key>")
@tracer.capture_method
def edit_subscription(key: str) -> dict[str, Any]:
    body = app.current_event.json_body or {}
    try:
        return subscriptions.edit_subscription(
            table, _user_id(), unquote(key), body, datetime.now(UTC)
        )
    except subscriptions.SubscriptionNotFound as err:
        raise logic.NotFound("Subscription not found.") from err
    except subscriptions.InvalidEdit as err:
        raise logic.BadRequest(str(err)) from err


@app.post("/ingest/keys")
@tracer.capture_method
def create_ingest_key() -> tuple[dict[str, Any], int]:
    groups = logic.parse_groups(_claims().get("cognito:groups"))
    try:
        key = ingest_keys.create_key(table, _user_id(), groups, datetime.now(UTC))
    except ingest_keys.NotAllowed as err:
        raise logic.Forbidden(str(err)) from err
    logger.info("ingest_key_created")  # never the key itself
    return {"key": key}, 201


@app.delete("/ingest/keys")
@tracer.capture_method
def revoke_ingest_key() -> Response:
    ingest_keys.revoke_key(table, _user_id())
    return Response(status_code=204)


@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_HTTP)
@tracer.capture_lambda_handler
def handler(event: dict, context: object) -> dict:
    return app.resolve(event, context)
