"""Mask personal data in document text before it goes to an LLM or a log.

What counts as PII here: card numbers (full or masked tails), long
account/reference numbers, and email addresses. Also the customer's name and address
(`address_lines`: delivery/billing blocks, postal-code lines, Textract's customer fields). Prices,
dates, item names, and the store's name stay, because extraction needs them.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Masked card tails: "****1234", "XXXX XXXX 1234", "************4321".
_MASKED_CARD = re.compile(r"(?:[*Xx#•]{2,}[ -]?)+\d{2,4}\b")
# Full card numbers: 13–19 digits, optionally grouped with spaces or dashes.
# Never runs into a price: "007874235186 3.48" is a product code and $3.48, not a card.
_CARD = re.compile(r"\b\d(?:[ -]?\d){12,18}\b(?![.,]\d)")
# Card tails written out: "VISA Ending in 6262", "ending 0042".
_ENDING_CARD = re.compile(r"(?i)(\b(?:ending|ends)(?:\s+in)?:?\s*)\d{4}\b")
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
    card_tail = masker("card", "[CARD]")
    text = _ENDING_CARD.sub(lambda m: m[1] + card_tail(m), text)
    text = _CARD.sub(masker("card", "[CARD]"), text)

    def long_digits(match: re.Match[str]) -> str:
        if len(match[0]) == 8 and _is_date(match[0]):
            return match[0]  # e.g. 20260930 on a receipt is a date, not an account
        counts["account"] = counts.get("account", 0) + 1
        return "[ACCOUNT]"

    text = _LONG_DIGITS.sub(long_digits, text)
    return Redaction(text=text, counts=counts)


def sensitive_spans(text: str) -> list[tuple[int, int]]:
    """Character spans to black out in an image: what `redact` masks, except a long number on a
    line that also shows a price, which is a product code (UPC), not an account."""
    spans = [m.span() for pattern in (_EMAIL, _MASKED_CARD, _CARD) for m in pattern.finditer(text)]
    spans += [(m.end(1), m.end()) for m in _ENDING_CARD.finditer(text)]
    if not _MONEY.search(text):
        spans += [
            m.span() for m in _LONG_DIGITS.finditer(text) if not (len(m[0]) == 8 and _is_date(m[0]))
        ]
    return sorted(spans)


def _is_date(digits: str) -> bool:
    for fmt in ("%Y%m%d", "%m%d%Y"):
        try:
            datetime.strptime(digits, fmt)  # noqa: DTZ007 (format check only, no timezone needed)
            return True
        except ValueError:
            continue
    return False


# --- the customer's name and address ------------------------------------------------------------

_ADDRESS_HEADING = re.compile(
    r"(?i)^\s*(?:(?:delivery|shipping|billing|mailing|customer)\s+address|"
    r"(?:ship(?:ped)?|bill(?:ed)?|sold|deliver(?:ed|y)?)\s+to)\b"
)
_POSTAL = re.compile(r"\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b|\b[A-Z]\d[A-Z]\s?\d[A-Z]\d\b")
_MONEY = re.compile(r"\d\.\d{2}\b")
_HOUSE_NUMBER = re.compile(r"^\s*\d+[A-Za-z]?\s+\w")
BLOCK_LINES = 3


def address_lines(
    lines: Sequence[str],
    boxes: Sequence[Any] | None = None,
    customer_values: Iterable[str] = (),
) -> set[int]:
    """Indexes of lines holding a customer's name or address, to mask whole.

    `boxes` (Textract line positions, same order as `lines`) let the delivery-address block be
    found by position, since reading order can interleave columns. `customer_values` are
    Textract's own customer fields (RECEIVER_NAME, RECEIVER_ADDRESS, ...).
    """
    found: set[int] = set()
    values = [v.strip().lower() for v in customer_values if len(v.strip()) >= 4]
    positioned = boxes is not None and len(boxes) == len(lines)
    for i, line in enumerate(lines):
        text = line.strip().lower()
        if values and len(text) >= 4 and any(text in v or v in text for v in values):
            found.add(i)
        if _POSTAL.search(line) and not _MONEY.search(line):
            found.add(i)
            if i > 0 and _HOUSE_NUMBER.match(lines[i - 1]):
                found.add(i - 1)
        if _ADDRESS_HEADING.match(line):
            below = _below(i, boxes) if positioned and boxes else range(i + 1, len(lines))
            for j in list(below)[:BLOCK_LINES]:
                if _MONEY.search(lines[j]) or not lines[j].strip():
                    break
                found.add(j)
    return found


def _below(i: int, boxes: Sequence[Any]) -> list[int]:
    """Lines just under heading i, in the same column, top to bottom."""
    heading = boxes[i]
    reach = heading.bottom + (BLOCK_LINES + 1) * 1.6 * heading.height
    under = [
        j
        for j, b in enumerate(boxes)
        if j != i
        and heading.top < b.top <= reach
        and abs(b.left - heading.left) <= 2 * heading.height + 0.02
    ]
    return sorted(under, key=lambda j: boxes[j].top)
