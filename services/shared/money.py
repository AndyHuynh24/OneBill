"""Money helpers. All money is integer cents; parsing goes through Decimal, never float."""

import re
from decimal import Decimal

# Optional "(", sign, "$", sign; digits with optional thousands separators; up to 2 decimals;
# optional ")" or trailing "-" (receipts often print discounts as "1.00-").
_AMOUNT = re.compile(
    r"""^\(?\s*(?P<lead>-)?\s*\$?\s*(?P<mid>-)?\s*
        (?P<int>\d{1,3}(?:,\d{3})+|\d+)?
        (?:\.(?P<frac>\d{1,2}))?
        \s*(?P<close>\))?\s*(?P<trail>-)?$""",
    re.VERBOSE,
)


def to_cents(raw: str | None) -> int | None:
    """Parse a printed amount like "$1,234.56", "1.00-" or "(2.50)" into integer cents.

    Returns None when the text isn't a plain amount, so callers can flag it instead of guessing.
    """
    if raw is None:
        return None
    text = raw.strip()
    match = _AMOUNT.match(text)
    if not match:
        return None
    if text.startswith("(") != bool(match["close"]):
        return None  # unbalanced parentheses
    if match["int"] is None and match["frac"] is None:
        return None  # a sign or "$" with no digits
    whole = (match["int"] or "0").replace(",", "")
    frac = (match["frac"] or "").ljust(2, "0")
    cents = int(Decimal(f"{whole}.{frac}") * 100)
    negative = bool(match["lead"] or match["mid"] or match["trail"] or match["close"])
    return -cents if negative else cents


def format_cents(cents: int) -> str:
    """Render cents for people: 2318 → "$23.18", -100 → "-$1.00"."""
    sign = "-" if cents < 0 else ""
    return f"{sign}${Decimal(abs(cents)) / 100:,.2f}"
