"""POST /ingest/email: emails sent by the user's Gmail script.

This is the API's one route without the Cognito JWT authorizer, because Apps Script can't sign
in. The caller is identified only by the ingest key in the Authorization header (looked up by
its hash); nothing in the body can choose the user.
"""

import json
import os
from datetime import UTC, datetime
from typing import Any

import boto3
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.event_handler import APIGatewayHttpResolver, Response, content_types
from aws_lambda_powertools.logging import correlation_paths
from botocore.config import Config

from subscriptions.ingest import verify_key
from subscriptions.process import BadEmail, process_email

logger = Logger()
tracer = Tracer()
app = APIGatewayHttpResolver()

table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
# API Gateway ends a request after 30 s: one Bedrock try within that, and the script resends
# next run if it fails (the email marker allows a retry).
bedrock = boto3.client(
    "bedrock-runtime",
    config=Config(
        read_timeout=20, connect_timeout=5, retries={"max_attempts": 1, "mode": "standard"}
    ),
)
MODEL_ID = os.environ["BEDROCK_MODEL_ID"]
MAX_REQUEST_BYTES = 512 * 1024


def _json(status: int, body: dict[str, Any]) -> Response:
    return Response(status_code=status, content_type=content_types.APPLICATION_JSON, body=body)


@app.post("/ingest/email")
@tracer.capture_method
def ingest_email() -> Response:
    user_id = verify_key(
        table, app.current_event.get_header_value("authorization"), datetime.now(UTC)
    )
    if user_id is None:
        return _json(401, {"message": "Unknown, expired, or missing ingest key."})
    try:
        payload = json.loads(app.current_event.body or "")
    except json.JSONDecodeError:
        return _json(400, {"message": "The body must be JSON."})
    try:
        result = process_email(table, bedrock, MODEL_ID, user_id, payload, datetime.now(UTC))
    except BadEmail as err:
        return _json(400, {"message": str(err)})
    # Outcome and counts only: no sender, subject, or service names in logs.
    logger.info(
        "email_ingested",
        extra={"status": result["status"], "subscriptions": result["subscriptions"]},
    )
    return _json(429 if result["status"] == "limit" else 200, result)


@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_HTTP)
@tracer.capture_lambda_handler
def handler(event: dict, context: object) -> dict:
    if len(event.get("body") or "") > MAX_REQUEST_BYTES:
        return {
            "statusCode": 413,
            "headers": {"content-type": "application/json"},
            "body": json.dumps({"message": "The email is too large."}),
        }
    return app.resolve(event, context)
