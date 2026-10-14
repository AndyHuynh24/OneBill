"""Estimated cost and speed of each AI read, for the logs and the owner's App usage card.

Costs are integer micro-dollars (1 µ$ = $0.000001) so money stays integer. They use list
prices (Bedrock and Textract pricing pages, us-east-1, checked 2026-10-14) and are estimates:
the AWS bill is the source of truth.
"""

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from shared import usage as app_counts

logger = logging.getLogger(__name__)

# µ$ per (input token, output token), keyed by the model name inside a model or inference
# profile ID ("us.anthropic.claude-haiku-4-5-20251001-v1:0"). Image tokens are input tokens.
MODEL_PRICES = {
    "claude-haiku-4-5": (1, 5),  # $1 / $5 per 1M tokens
}
TEXTRACT_PAGE = 10_000  # AnalyzeExpense: $0.01 a page

USAGE_TTL_DAYS = 7
MONTHLY_USAGE_TTL_DAYS = 40


def estimate(model_id: str, usage: Mapping[str, int], textract_pages: int = 0) -> int:
    """Estimated µ$ for one read: the model's tokens plus any Textract pages. An unpriced model
    counts as 0 (logged), never an error: a new model must not break reading."""
    tokens_cost = 0
    prices = next((p for name, p in MODEL_PRICES.items() if name in model_id), None)
    if prices is None:
        logger.warning("unpriced_model", extra={"modelId": model_id})
    else:
        tokens_cost = usage["inputTokens"] * prices[0] + usage["outputTokens"] * prices[1]
    return tokens_cost + textract_pages * TEXTRACT_PAGE


def tokens_per_second(output_tokens: int, model_ms: Sequence[int]) -> int | None:
    """Output tokens per second of waiting for the model (all calls of one read)."""
    waited = sum(model_ms)
    return round(output_tokens * 1000 / waited) if waited > 0 else None


def record(
    table: Any,
    now: datetime,
    model_id: str,
    usage: Mapping[str, int],
    model_ms: Sequence[int],
    textract_pages: int = 0,
) -> dict[str, Any]:
    """Add one read to today's and this month's app totals (best effort) and return its cost
    and speed for the caller's log line. Numbers only: no user ID, no document content."""
    cost = estimate(model_id, usage, textract_pages)
    amounts = {
        "aiReads": 1,
        "aiCostMicroUsd": cost,
        "inputTokens": usage["inputTokens"],
        "outputTokens": usage["outputTokens"],
    }
    app_counts.count_many(table, now.date().isoformat(), amounts, now, USAGE_TTL_DAYS)
    app_counts.count_many(table, now.strftime("%Y-%m"), amounts, now, MONTHLY_USAGE_TTL_DAYS)
    return {
        "costMicroUsd": cost,
        "outputTokensPerSec": tokens_per_second(usage["outputTokens"], model_ms),
    }
