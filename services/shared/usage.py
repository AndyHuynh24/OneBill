"""App-wide usage counts (`GLOBAL / USAGE#<day or month>`) for the owner's App usage card.

Display only: a failed count must never fail the request that already used its quota, so
errors are logged (no user data) and swallowed.
"""

import logging
from datetime import datetime, timedelta
from typing import Any

from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


def count(table: Any, period: str, name: str, amount: int, now: datetime, ttl_days: int) -> None:
    """Add `amount` to GLOBAL's `name` for `period` (a day or a month); best effort."""
    try:
        table.update_item(
            Key={"PK": "GLOBAL", "SK": f"USAGE#{period}"},
            UpdateExpression="ADD #n :n SET expiresAt = if_not_exists(expiresAt, :exp)",
            ExpressionAttributeNames={"#n": name},
            ExpressionAttributeValues={
                ":n": amount,
                ":exp": int((now + timedelta(days=ttl_days)).timestamp()),
            },
        )
    except ClientError as err:
        logger.warning(
            "usage_count_failed",
            extra={"counter": name, "period": period, "error": err.response["Error"]["Code"]},
        )
