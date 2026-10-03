from documents.confidence import CHECK_BELOW, CONFIDENT_AT, assess
from documents.reconcile import Line

OCR = ["TARGET", "BANANAS 1.29", "MILK 4.19", "SUBTOTAL 5.48", "TAX 0.44", "TOTAL 5.92"]
LINES = [
    Line(1, "BANANAS", "item", 129),
    Line(2, "MILK", "item", 419),
    Line(3, "SUBTOTAL", "subtotal", 548),
    Line(4, "TAX", "tax", 44),
    Line(5, "TOTAL", "total", 592),
]


def sure(n=None, value=99.0):
    return [value] * (len(OCR) if n is None else n)


def test_clear_print_that_adds_up_first_time_is_confident():
    result = assess(LINES, OCR, sure(), suspect_rows=[], attempts=1, adds_up=True)

    assert result == {"level": "confident", "checks": []}


def test_a_hard_to_read_amount_is_flagged_by_name():
    confidences = sure()
    confidences[4] = CHECK_BELOW - 20  # "TAX 0.44" was blurry

    result = assess(LINES, OCR, confidences, suspect_rows=[], attempts=1, adds_up=True)

    assert result["level"] == "check"
    assert result["checks"] == [{"row": 4, "reason": "Hard to read on the photo"}]


def test_an_amount_not_found_on_the_document_is_flagged():
    lines = [*LINES[:1], Line(2, "MILK", "item", 491), *LINES[2:]]  # 4.91 isn't printed

    result = assess(lines, OCR, sure(), suspect_rows=[], attempts=1, adds_up=False)

    assert result["checks"] == [{"row": 2, "reason": "Not found on the receipt"}]


def test_lines_the_math_points_at_are_flagged():
    result = assess(LINES, OCR, sure(), suspect_rows=[3], attempts=1, adds_up=False)

    assert result["checks"] == [{"row": 3, "reason": "Doesn't fit the totals"}]


def test_totals_that_dont_add_up_without_a_culprit_still_ask_for_a_check():
    result = assess(LINES, OCR, sure(), suspect_rows=[], attempts=2, adds_up=False)

    assert result == {"level": "check", "checks": []}


def test_a_key_total_read_with_middling_confidence_is_only_mostly_sure():
    confidences = sure()
    confidences[5] = (CHECK_BELOW + CONFIDENT_AT) / 2  # "TOTAL 5.92": readable, not crisp

    result = assess(LINES, OCR, confidences, suspect_rows=[], attempts=1, adds_up=True)

    assert result == {"level": "mostly", "checks": []}


def test_needing_a_repair_pass_means_mostly_sure_not_confident():
    result = assess(LINES, OCR, sure(), suspect_rows=[], attempts=2, adds_up=True)

    assert result["level"] == "mostly"


def test_an_email_has_no_ocr_confidence_and_can_still_be_confident():
    result = assess(LINES, OCR, None, suspect_rows=[], attempts=1, adds_up=True)

    assert result["level"] == "confident"


def test_payment_change_and_info_lines_are_never_flagged():
    confidences = sure(len(OCR) + 1, value=99.0)
    ocr = [*OCR, "CASH 10.00"]
    confidences[-1] = 10.0
    lines = [*LINES, Line(6, "CASH", "payment", 1000)]

    result = assess(lines, ocr, confidences, suspect_rows=[], attempts=1, adds_up=True)

    assert result["checks"] == []
