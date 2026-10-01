"""Documents API Lambda: POST/GET /documents, GET /documents/{docId}, GET /obligations.

Thin: read the verified user ID and groups from the JWT claims, validate the request shape,
call logic, map errors to HTTP status codes.
"""

import os
from datetime import UTC, datetime
from typing import Any

import boto3
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.event_handler import APIGatewayHttpResolver, Response, content_types
from aws_lambda_powertools.event_handler.exceptions import UnauthorizedError
from aws_lambda_powertools.logging import correlation_paths
from botocore.config import Config

from api.documents import logic
from shared.dates import app_today, parse_iso_date

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
        table, s3, BUCKET, user_id, groups, filename, content_type, size, now=datetime.now(UTC)
    )
    logger.info("upload_created", extra={"docId": result["docId"]})
    return result, 201


@app.get("/documents")
@tracer.capture_method
def list_documents() -> dict[str, Any]:
    return {"documents": logic.list_documents(table, _user_id())}


@app.get("/documents/<doc_id>")
@tracer.capture_method
def get_document(doc_id: str) -> dict[str, Any]:
    return logic.get_document(table, _user_id(), doc_id)


@app.get("/obligations")
@tracer.capture_method
def list_obligations() -> dict[str, Any]:
    user_id = _user_id()
    event = app.current_event
    raw_days = event.get_query_string_value("withinDays", str(DEFAULT_OBLIGATION_WINDOW_DAYS))
    if not raw_days.isdigit():
        raise logic.BadRequest("withinDays must be a whole number.")
    # The browser may pass its own local date; otherwise use the app time zone.
    today = parse_iso_date(event.get_query_string_value("from")) or app_today(datetime.now(UTC))
    return {"obligations": logic.list_obligations(table, user_id, today, int(raw_days))}


@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_HTTP)
@tracer.capture_lambda_handler
def handler(event: dict, context: object) -> dict:
    return app.resolve(event, context)
