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


def to_cents_loose(raw: str | None) -> int | None:
    """Amounts as emails print them, in any common style: "$15", "$1,299", "€10,99",
    "1.234,56 €", "99.000 ₫", "15.49 USD". None if it isn't clearly one amount.

    A single separator followed by 1–2 digits is a decimal point; followed by exactly 3 digits
    it groups thousands. With both "." and ",", the last one is the decimal point.
    """
    if not raw:
        return None
    negative = bool(re.search(r"^\s*\(|-", raw))
    number = re.sub(r"[^\d.,]", "", raw).strip(".,")
    if not re.search(r"\d", number):
        return None
    if "." in number and "," in number:
        decimal = "." if number.rfind(".") > number.rfind(",") else ","
        whole, frac = number.rsplit(decimal, 1)
        groups = whole.split("," if decimal == "." else ".")
    else:
        sep = "." if "." in number else ","
        parts = number.split(sep) if sep in number else [number]
        if len(parts) == 2 and 1 <= len(parts[1]) <= 2:
            whole, frac, groups = parts[0], parts[1], [parts[0]]
        else:
            whole, frac, groups = number.replace(sep, ""), "", parts
    if len(frac) > 2 or not all(groups) or any(len(g) != 3 for g in groups[1:]):
        return None
    if len(groups) > 1 and not 1 <= len(groups[0]) <= 3:
        return None
    digits = "".join(groups) if len(groups) > 1 else whole
    if not digits.isdigit() or (frac and not frac.isdigit()):
        return None
    cents = int(Decimal(f"{digits}.{frac.ljust(2, '0')}") * 100)
    return -cents if negative else cents


def format_cents(cents: int) -> str:
    """Render cents for people: 2318 → "$23.18", -100 → "-$1.00"."""
    sign = "-" if cents < 0 else ""
    return f"{sign}${Decimal(abs(cents)) / 100:,.2f}"
