from documents.grounding import ungrounded
from documents.reconcile import Line

OCR = [
    "OSTERIA 177",
    "3 LASAGNA CINGHIAL 72.00",
    "Subtotal 499.50",
    "Sales Tax 33.74",
    "Service Chrg 99.90",
    "10:26PM Total 633.14",
    "COUPON 2.00-",
    "BIG ITEM 1,234.56",
]


def line(desc, cents, kind="item", row=1):
    return Line(row=row, description=desc, kind=kind, amount_cents=cents)


def test_printed_amounts_are_grounded():
    lines = [line("LASAGNA", 7200), line("Sales Tax", 3374, "tax"), line("Total", 63314, "total")]

    assert ungrounded(lines, OCR) == []


def test_invented_amount_is_caught():
    """The round-2 eval: the model made up a "Service Chrg 1.50" line to force the math."""
    invented = line("Service Chrg", 150, "fee", row=9)

    assert ungrounded([line("LASAGNA", 7200), invented], OCR) == [invented]


def test_negative_and_thousands_formats_match():
    assert ungrounded([line("COUPON", -200, "discount"), line("BIG ITEM", 123456)], OCR) == []


def test_amount_must_not_match_inside_a_longer_number():
    assert ungrounded([line("X", 3374 // 100 * 100)], ["Sales Tax 133.00"]) == [line("X", 3300)]


def test_zero_amounts_are_not_checked():
    """ "TOTAL TAX .00" style lines: zero is too ambiguous to ground and harmless."""
    assert ungrounded([line("TOTAL TAX", 0, "tax")], ["TOTAL TAX .00"]) == []
