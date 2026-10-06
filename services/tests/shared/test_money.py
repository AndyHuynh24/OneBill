import pytest

from shared.money import format_cents, to_cents


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("23.18", 2318),
        ("$23.18", 2318),
        ("1,234.56", 123456),
        ("$ 1,234.56", 123456),
        ("0.99", 99),
        ("5", 500),
        ("5.5", 550),
        ("-1.00", -100),
        ("1.00-", -100),  # trailing minus: common for discounts on receipts
        ("(1.00)", -100),  # accounting-style negative
        ("-$2.50", -250),
        ("$-2.50", -250),
        (" 12.30 ", 1230),
        (".00", 0),  # Whole Foods prints "TOTAL TAX .00"
        (".99", 99),
        ("-.50", -50),
    ],
)
def test_to_cents_parses_receipt_amounts(raw, expected):
    assert to_cents(raw) == expected


@pytest.mark.parametrize("raw", ["", "abc", "1.2.3", "12.345", None, "N/A", ".", "-", "$"])
def test_to_cents_rejects_unparseable_amounts(raw):
    assert to_cents(raw) is None


def test_to_cents_returns_int_never_float():
    assert isinstance(to_cents("0.10"), int)
    # 0.1 + 0.2 style float errors can't happen: Decimal arithmetic end to end
    assert to_cents("0.10") + to_cents("0.20") == 30


@pytest.mark.parametrize(
    ("cents", "expected"),
    [(2318, "$23.18"), (5, "$0.05"), (-100, "-$1.00"), (123456, "$1,234.56"), (0, "$0.00")],
)
def test_format_cents(cents, expected):
    assert format_cents(cents) == expected


@pytest.mark.parametrize(
    ("raw", "cents"),
    [
        ("$15.49", 1549),
        ("$15", 1500),  # whole dollars
        ("$1,299", 129900),  # thousands, no cents
        ("€10,99", 1099),  # comma decimals (Europe)
        ("1.234,56 €", 123456),
        ("1,234.56", 123456),
        ("99.000 ₫", 9900000),  # dot thousands (Vietnam)
        ("15.49 USD", 1549),
        ("US$9.99", 999),
        ("-$2.00", -200),
    ],
)
def test_loose_amounts_from_emails(raw, cents):
    from shared.money import to_cents_loose

    assert to_cents_loose(raw) == cents


@pytest.mark.parametrize("raw", [None, "", "free", "1,2,3", "$", "12.3.4"])
def test_loose_amounts_reject_non_amounts(raw):
    from shared.money import to_cents_loose

    assert to_cents_loose(raw) is None
