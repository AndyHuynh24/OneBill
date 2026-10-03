"""Grounding: every amount the model records must actually be printed on the document.

The model copies amounts as text and code checks them against the OCR text. An amount that
appears nowhere was invented (the eval caught the model making up a "Service Chrg 1.50" line
to force the arithmetic), so it's reported instead of silently accepted.
"""

import re

from documents.reconcile import Line
from shared.money import to_cents


def _printed_forms(cents: int) -> list[str]:
    """How an amount can appear in print: 15.49 · 1,234.56 · .99 · 10,99 · 1.234,56 · and,
    for whole amounts, 15 · 1,299 · 99.000 (no cents shown)."""
    whole, frac = divmod(abs(cents), 100)
    us = f"{whole:,}"
    eu = us.replace(",", ".")
    forms = {f"{whole}.{frac:02d}", f"{us}.{frac:02d}", f"{whole},{frac:02d}", f"{eu},{frac:02d}"}
    if whole == 0:
        forms.add(f".{frac:02d}")
    if frac == 0:
        forms |= {str(whole), us, eu}
    return sorted(forms, key=len, reverse=True)


def is_printed(cents: int, text: str) -> bool:
    """Is this amount printed in the text (as 15.49, 1,234.56, or .99)?"""
    # Not part of a longer number: no digit, comma, or point right before; no digit (or
    # decimal part) right after, so 15 isn't "found" inside 15.49.
    return any(
        re.search(rf"(?<![\d.,]){re.escape(form)}(?!\d|[.,]\d)", text)
        for form in _printed_forms(cents)
    )


def ungrounded(lines: list[Line], ocr_lines: list[str]) -> list[Line]:
    """Lines whose amount doesn't appear in the document text (zero amounts are skipped)."""
    text = "\n".join(ocr_lines)
    return [
        line for line in lines if line.amount_cents != 0 and not is_printed(line.amount_cents, text)
    ]


# "$1.02 from savings", "You save $1.50", "Savings: $0.99"
_SAVING = re.compile(
    r"\$?\s?(\d[\d,]*\.\d{2})\s*(?:from\s+savings|saved)"
    r"|(?:you\s+save|savings?:?|save)\s*\$?\s?(\d[\d,]*\.\d{2})",
    re.IGNORECASE,
)


def printed_savings(ocr_lines: list[str]) -> set[int]:
    """Per-item savings printed on online orders, in cents (evidence for crossed-out prices)."""
    found = set()
    for match in _SAVING.finditer("\n".join(ocr_lines)):
        cents = to_cents(match.group(1) or match.group(2))
        if cents:
            found.add(cents)
    return found
