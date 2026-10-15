"""POST /assistant/ask: what a request may contain, and how many questions it may use.

Limits (cost review): 10 questions a day per user (the owner 20), and a monthly cap across
everyone (100 in production, 30 in staging: `MONTHLY_QUESTIONS`), counted on the existing
`USAGE#` items as `questions`. Worst case about $0.03 a question, so a few dollars a month.
The model is only called after a question is counted, so a refused one costs nothing.
"""

import os
from datetime import datetime, timedelta
from typing import Any

from botocore.exceptions import ClientError

from api.documents.logic import BadRequest, QuotaExceeded

DAILY_QUESTIONS = 10
OWNER_DAILY_QUESTIONS = 20
MONTHLY_APP_QUESTIONS = int(os.environ.get("MONTHLY_QUESTIONS", "100"))
MAX_QUESTION = 500
MAX_TURN = 500
MAX_HISTORY = 6
DAY_TTL_DAYS = 7
MONTH_TTL_DAYS = 40


def validated(body: Any) -> tuple[str, list[dict[str, str]]]:
    """The question, and the last few turns of the conversation (text only, user first,
    alternating, ending with an answer). Tool results are never accepted from the client."""
    if not isinstance(body, dict):
        raise BadRequest("The body must be a JSON object.")
    question = body.get("question")
    question = " ".join(question.split()) if isinstance(question, str) else ""
    if not 0 < len(question) <= MAX_QUESTION:
        raise BadRequest(f"Ask a question of 1–{MAX_QUESTION} characters.")
    raw = body.get("history", [])
    if not isinstance(raw, list):
        raise BadRequest("history must be a list of turns.")
    turns = []
    for turn in raw:
        role = turn.get("role") if isinstance(turn, dict) else None
        text = turn.get("text") if isinstance(turn, dict) else None
        if role not in {"user", "assistant"} or not isinstance(text, str) or not text.strip():
            raise BadRequest("Each turn needs a role (user or assistant) and some text.")
        if len(text) > MAX_TURN:
            raise BadRequest(f"Each turn can be at most {MAX_TURN} characters.")
        turns.append({"role": role, "text": text.strip()})
    turns = turns[-MAX_HISTORY:]
    while turns and turns[0]["role"] != "user":
        turns = turns[1:]
    if any(a["role"] == b["role"] for a, b in zip(turns, turns[1:], strict=False)):
        raise BadRequest("history must alternate between user and assistant.")
    if turns and turns[-1]["role"] != "assistant":
        raise BadRequest("history must end with an answer.")
    return question, turns


def use_question(table: Any, user_id: str, owner: bool, now: datetime) -> int:
    """Count one question (yours today, then everyone's this month). Returns how many you have
    left today. Over a limit: QuotaExceeded, and nothing is counted."""
    limit = OWNER_DAILY_QUESTIONS if owner else DAILY_QUESTIONS
    day, month = now.date().isoformat(), now.strftime("%Y-%m")
    used = _add(table, f"USER#{user_id}", day, limit, now, DAY_TTL_DAYS)
    if used is None:
        raise QuotaExceeded(f"You've asked today's {limit} questions. Ask again tomorrow.")
    if _add(table, "GLOBAL", month, MONTHLY_APP_QUESTIONS, now, MONTH_TTL_DAYS) is None:
        _refund(table, f"USER#{user_id}", day)
        raise QuotaExceeded("OneBill has answered this month's questions. Ask again on the 1st.")
    return limit - used


def _add(table: Any, pk: str, period: str, limit: int, now: datetime, ttl: int) -> int | None:
    try:
        item = table.update_item(
            Key={"PK": pk, "SK": f"USAGE#{period}"},
            UpdateExpression="ADD questions :one SET expiresAt = if_not_exists(expiresAt, :exp)",
            ConditionExpression="attribute_not_exists(questions) OR questions < :limit",
            ExpressionAttributeValues={
                ":one": 1,
                ":limit": limit,
                ":exp": int((now + timedelta(days=ttl)).timestamp()),
            },
            ReturnValues="UPDATED_NEW",
        )["Attributes"]
    except ClientError as err:
        if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return None
        raise
    return int(item["questions"])


def _refund(table: Any, pk: str, period: str) -> None:
    try:
        table.update_item(
            Key={"PK": pk, "SK": f"USAGE#{period}"},
            UpdateExpression="ADD questions :minus",
            ConditionExpression="questions > :zero",
            ExpressionAttributeValues={":minus": -1, ":zero": 0},
        )
    except ClientError as err:
        if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
