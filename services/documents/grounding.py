"""Grounding: every amount the model records must actually be printed on the document.

The model copies amounts as text and code checks them against the OCR text. An amount that
appears nowhere was invented (the eval caught the model making up a "Service Chrg 1.50" line
to force the arithmetic), so it's reported instead of silently accepted.
"""

import re

from documents.reconcile import Line


def _printed_forms(cents: int) -> list[str]:
    whole, frac = divmod(abs(cents), 100)
    forms = [f"{whole}.{frac:02d}"]
    if whole >= 1000:
        forms.append(f"{whole:,}.{frac:02d}")
    if whole == 0:
        forms.append(f".{frac:02d}")
    return forms


def ungrounded(lines: list[Line], ocr_lines: list[str]) -> list[Line]:
    """Lines whose amount doesn't appear in the document text (zero amounts are skipped)."""
    text = "\n".join(ocr_lines)
    missing = []
    for line in lines:
        if line.amount_cents == 0:
            continue
        # Not part of a longer number: no digit, comma, or point right before; no digit after.
        found = any(
            re.search(rf"(?<![\d.,]){re.escape(form)}(?!\d)", text)
            for form in _printed_forms(line.amount_cents)
        )
        if not found:
            missing.append(line)
    return missing
