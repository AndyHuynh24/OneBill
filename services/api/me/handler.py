"""GET /me Lambda entry point. Thin: pull the verified user ID from the JWT claims, call logic."""

import os

import boto3
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.event_handler import APIGatewayHttpResolver
from aws_lambda_powertools.event_handler.exceptions import UnauthorizedError
from aws_lambda_powertools.logging import correlation_paths

from api.me.logic import get_or_create_profile

logger = Logger()
tracer = Tracer()
app = APIGatewayHttpResolver()

# Module level: reused across warm invocations.
table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])


@app.get("/me")
@tracer.capture_method
def get_me() -> dict[str, str]:
    # The JWT authorizer already verified the token; `sub` is the only trusted user ID.
    # Never read a user ID from the body, query string, or headers.
    user_id = app.current_event.request_context.authorizer.jwt_claim.get("sub")
    if not user_id:
        raise UnauthorizedError("Unauthorized")
    return get_or_create_profile(table, user_id)


@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_HTTP)
@tracer.capture_lambda_handler
def handler(event: dict, context: object) -> dict:
    return app.resolve(event, context)
