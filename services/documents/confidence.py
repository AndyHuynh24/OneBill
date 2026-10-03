"""How sure we are about a reading, from evidence, not from the model's opinion (phase 2d).

Per money line: flagged if the OCR read its amount with low confidence, if the amount isn't
printed on the document, or if the math points at it. Overall: "confident" only when nothing is
flagged, everything adds up on the first reading, and the key totals were read crisply;
"check" when anything is flagged or the totals don't add up; "mostly" in between.
"""

from typing import Any

from documents.grounding import is_printed
from documents.reconcile import Line

CHECK_BELOW = 85.0  # OCR confidence (0–100) under which an amount is flagged
CONFIDENT_AT = 95.0  # key totals must be read at least this crisply to say "confident"
KEY_KINDS = frozenset({"subtotal", "tax", "total", "balance_due"})
# Payment, change, and info lines don't affect what anyone owes: never flagged.
CHECKED_KINDS = frozenset(
    {"item", "discount", "fee", "tax", "tip", "subtotal", "total", "balance_due"}
)

HARD_TO_READ = "Hard to read on the photo"
NOT_FOUND = "Not found on the receipt"
OFF_TOTALS = "Doesn't fit the totals"


def assess(
    lines: list[Line],
    ocr_lines: list[str],
    ocr_confidences: list[float] | None,
    *,
    suspect_rows: list[int],
    attempts: int,
    adds_up: bool,
) -> dict[str, Any]:
    """`ocr_confidences` align with `ocr_lines` (None for emails, which have no OCR)."""
    text = "\n".join(ocr_lines)
    checks: list[dict[str, Any]] = []
    key_readings: list[float] = []
    for line in lines:
        if line.kind not in CHECKED_KINDS:
            continue
        reading = _reading(line.amount_cents, ocr_lines, ocr_confidences)
        if line.kind in KEY_KINDS and reading is not None:
            key_readings.append(reading)
        if line.amount_cents != 0 and not is_printed(line.amount_cents, text):
            checks.append({"row": line.row, "reason": NOT_FOUND})
        elif reading is not None and reading < CHECK_BELOW:
            checks.append({"row": line.row, "reason": HARD_TO_READ})
        elif line.row in suspect_rows:
            checks.append({"row": line.row, "reason": OFF_TOTALS})

    if checks or not adds_up:
        level = "check"
    elif attempts == 1 and all(r >= CONFIDENT_AT for r in key_readings):
        level = "confident"
    else:
        level = "mostly"
    return {"level": level, "checks": checks}


def _reading(cents: int, ocr_lines: list[str], confidences: list[float] | None) -> float | None:
    """The lowest OCR confidence among the lines printing this amount (the safe side)."""
    if not confidences or len(confidences) != len(ocr_lines) or cents == 0:
        return None
    found = [c for line, c in zip(ocr_lines, confidences, strict=True) if is_printed(cents, line)]
    return min(found) if found else None
