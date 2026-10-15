"""POST /assistant/ask: "Ask OneBill" — a question about your own bills, answered by an agent
loop over read-only tools (assistant/), with semantic search over your bills when it's set up.

Logs carry counts, tokens, and timings only: never the question, the answer, or bill text.
"""

import os
import time
from datetime import UTC, datetime
from typing import Any

import boto3
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.event_handler import APIGatewayHttpResolver, Response, content_types
from aws_lambda_powertools.event_handler.exceptions import UnauthorizedError
from aws_lambda_powertools.logging import correlation_paths
from botocore.config import Config

from api.assistant import logic
from api.documents.logic import OWNERS_GROUP, DocumentsError, parse_groups
from assistant import loop, search, tools
from assistant.prompt import PROMPT_VERSION
from shared.dates import app_today

logger = Logger()
tracer = Tracer()
app = APIGatewayHttpResolver()

table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
# Each model call must finish well inside the 25 s budget (API Gateway stops at 30 s).
bedrock = boto3.client(
    "bedrock-runtime",
    config=Config(read_timeout=12, retries={"mode": "standard", "max_attempts": 1}),
)
vectors = boto3.client("s3vectors")
MODEL_ID = os.environ["BEDROCK_MODEL_ID"]
INDEX_ARN = os.environ.get("VECTOR_INDEX_ARN", "")


def _user_id() -> str:
    # The JWT authorizer verified the token; `sub` is the only trusted identity.
    user_id = app.current_event.request_context.authorizer.jwt_claim.get("sub")
    if not user_id:
        raise UnauthorizedError("Unauthorized")
    return user_id


@app.exception_handler(DocumentsError)
def _error(err: DocumentsError) -> Response:
    return Response(
        status_code=err.status,
        content_type=content_types.APPLICATION_JSON,
        body={"message": err.message},
    )


@app.post("/assistant/ask")
@tracer.capture_method
def ask() -> dict[str, Any]:
    user_id, now = _user_id(), datetime.now(UTC)
    question, history = logic.validated(app.current_event.json_body)
    groups = parse_groups(
        app.current_event.request_context.authorizer.jwt_claim.get("cognito:groups")
    )
    left = logic.use_question(table, user_id, OWNERS_GROUP in groups, now)

    ctx = tools.Context(
        table=table,
        user_id=user_id,
        today=app_today(now),
        search=search.searcher(bedrock, vectors, INDEX_ARN, user_id) if INDEX_ARN else None,
    )
    started = time.monotonic()
    answer = loop.ask(bedrock, MODEL_ID, ctx, question, history)
    logger.info(
        "question_answered",
        extra={
            "calls": answer.calls,
            "toolCalls": answer.tool_calls,
            "inputTokens": answer.input_tokens,
            "outputTokens": answer.output_tokens,
            "ms": round((time.monotonic() - started) * 1000),
            "stopped": answer.stopped,
            "sources": len(answer.sources),
            "promptVersion": PROMPT_VERSION,
        },
    )
    return {
        "answer": answer.text,
        "sources": _sources(user_id, answer.sources),
        "questionsLeft": left,
    }


def _sources(user_id: str, doc_ids: list[str]) -> list[dict[str, Any]]:
    """The bills an answer used, to show and open: the caller's own, still there."""
    out = []
    for doc_id in doc_ids:
        doc = table.get_item(Key={"PK": f"USER#{user_id}", "SK": f"DOC#{doc_id}"}).get("Item")
        if doc:
            out.append(
                {
                    "docId": doc_id,
                    "vendor": doc.get("vendor"),
                    "date": doc.get("documentDate"),
                    "totalCents": None if doc.get("totalCents") is None else int(doc["totalCents"]),
                    "currency": doc.get("currency"),
                }
            )
    return out


@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_HTTP)
@tracer.capture_lambda_handler
def handler(event: dict, context: object) -> dict:
    return app.resolve(event, context)
