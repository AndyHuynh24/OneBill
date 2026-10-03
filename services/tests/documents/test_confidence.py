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

    # Not printed, and swapping two digits (4.91 → 4.19) makes it add up: both are said.
    assert result["checks"] == [
        {"row": 2, "reason": "Not found on the receipt", "suggestedCents": 419}
    ]


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


# --- one-digit slips: when the totals are off, which single misread digit would fix them? -----

from documents.confidence import SLIP, after_edit, one_digit_slips  # noqa: E402

WALMART = [  # receipt 6, freshly scanned: tax printed $16.51, OCR read "$16.61"
    Line(1, "GROCERIES", "item", 16462),
    Line(2, "SUBTOTAL", "subtotal", 16462),
    Line(3, "HST 13.0000%", "tax", 1661),
    Line(4, "TOTAL", "total", 18113),
]


def test_a_misread_digit_is_found_and_the_fix_suggested():
    slips = one_digit_slips(WALMART)

    by_row = {s["row"]: s["suggestedCents"] for s in slips}
    assert by_row[3] == 1651  # tax $16.61 → $16.51 makes it add up
    assert all(s["reason"] == SLIP for s in slips)


def test_the_least_clearly_read_candidate_comes_first():
    confidences = {1: 99.0, 2: 99.0, 3: 70.0, 4: 98.0}

    slips = one_digit_slips(WALMART, confidences)

    assert slips[0]["row"] == 3


def test_a_dropped_digit_counts_as_a_slip_too():
    """OCR read "$6.51" for "$16.51" (receipt 6, first scan)."""
    lines = [*WALMART[:2], Line(3, "HST", "tax", 651), WALMART[3]]

    assert {"row": 3, "reason": SLIP, "suggestedCents": 1651} in one_digit_slips(lines)


def test_nothing_is_suggested_when_it_already_adds_up():
    lines = [*WALMART[:2], Line(3, "HST", "tax", 1651), WALMART[3]]

    assert one_digit_slips(lines) == []


def test_at_most_three_suggestions():
    assert len(one_digit_slips(WALMART)) <= 3


def test_assess_flags_the_slip_candidates_when_the_totals_dont_add_up():
    ocr = ["GROCERIES 164.62", "SUBTOTAL 164.62", "HST 13.0000% 16.61", "TOTAL 181.13"]
    confidences = [99.0, 99.0, 90.0, 99.0]  # tax read fairly clearly: not "hard to read"

    result = assess(WALMART, ocr, confidences, suspect_rows=[], attempts=1, adds_up=False)

    assert result["level"] == "check"
    assert result["checks"][0] == {"row": 3, "reason": SLIP, "suggestedCents": 1651}


# --- after the user edits lines on the bill -------------------------------------------------------


def test_fixing_the_flagged_line_so_it_adds_up_marks_the_bill_checked():
    before = {"level": "check", "checks": [{"row": 3, "reason": SLIP, "suggestedCents": 1651}]}
    fixed = [*WALMART[:2], Line(3, "HST 13.0000%", "tax", 1651), WALMART[3]]

    assert after_edit(before, WALMART, fixed) == {"level": "confirmed", "checks": []}


def test_flags_on_lines_the_user_didnt_touch_stay():
    before = {"level": "check", "checks": [{"row": 1, "reason": "Hard to read on the photo"}]}
    fixed = [*WALMART[:2], Line(3, "HST 13.0000%", "tax", 1651), WALMART[3]]

    result = after_edit(before, WALMART, fixed)

    assert result == {
        "level": "check",
        "checks": [{"row": 1, "reason": "Hard to read on the photo"}],
    }


def test_an_edit_that_still_doesnt_add_up_gets_fresh_suggestions():
    wrong = [*WALMART[:2], Line(3, "HST 13.0000%", "tax", 1671), WALMART[3]]

    result = after_edit({"level": "check", "checks": []}, WALMART, wrong)

    assert result["level"] == "check"
    assert {"row": 3, "reason": SLIP, "suggestedCents": 1651} in result["checks"]
