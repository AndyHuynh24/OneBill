"""Time-ordered IDs: 13-digit epoch milliseconds + 8 random hex chars (e.g. 1790000000000a1b2c3d4).

Sorting IDs as strings sorts by creation time, so `begins_with DOC#` queries return documents
in order without a separate index.
"""

import re
import secrets
import time

_ID = re.compile(r"^\d{13}[0-9a-f]{8}$")


def new_id(now_ms: int | None = None) -> str:
    ms = int(time.time() * 1000) if now_ms is None else now_ms
    return f"{ms:013d}{secrets.token_hex(4)}"


def is_valid_id(value: str | None) -> bool:
    return bool(value) and _ID.fullmatch(value) is not None
