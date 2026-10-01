"""Mask personal data in document text before it goes to an LLM or a log.

What counts as PII here (Phase 1, decision D8): card numbers (full or masked tails), long
account/reference numbers, and email addresses. Prices, dates, item names, and the store's own
address and phone stay, because extraction needs them.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Masked card tails: "****1234", "XXXX XXXX 1234", "************4321".
_MASKED_CARD = re.compile(r"(?:[*Xx#•]{2,}[ -]?)+\d{2,4}\b")
# Full card numbers: 13–19 digits, optionally grouped with spaces or dashes.
_CARD = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")
# Account / reference numbers: 8+ consecutive digits.
_LONG_DIGITS = re.compile(r"\b\d{8,}\b")


@dataclass(frozen=True)
class Redaction:
    text: str
    counts: dict[str, int] = field(default_factory=dict)


def redact(text: str) -> Redaction:
    counts: dict[str, int] = {}

    def masker(label: str, token: str):
        def replace(match: re.Match[str]) -> str:
            counts[label] = counts.get(label, 0) + 1
            return token

        return replace

    text = _EMAIL.sub(masker("email", "[EMAIL]"), text)
    text = _MASKED_CARD.sub(masker("card", "[CARD]"), text)
    text = _CARD.sub(masker("card", "[CARD]"), text)

    def long_digits(match: re.Match[str]) -> str:
        if len(match[0]) == 8 and _is_date(match[0]):
            return match[0]  # e.g. 20260930 on a receipt is a date, not an account
        counts["account"] = counts.get("account", 0) + 1
        return "[ACCOUNT]"

    text = _LONG_DIGITS.sub(long_digits, text)
    return Redaction(text=text, counts=counts)


def _is_date(digits: str) -> bool:
    for fmt in ("%Y%m%d", "%m%d%Y"):
        try:
            datetime.strptime(digits, fmt)  # noqa: DTZ007 (format check only, no timezone needed)
            return True
        except ValueError:
            continue
    return False
