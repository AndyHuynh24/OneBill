"""A free check before the model: does this email look like it's about a subscription?

The Gmail script searches broadly (receipts, invoices, renewals), so many emails it sends are
one-off purchases or promotions. Each model call costs ~0.4¢ and counts toward the daily cap,
so an email needs both a price and a word about recurring billing before the model reads it.
Emails the user labelled by hand skip this check.
"""

import re

_PRICE = re.compile(
    r"[$€£¥]\s?\d[\d,]*(?:\.\d{2})?"  # $15.49, € 10,99
    r"|\b(?:USD|EUR|GBP|CAD|AUD|JPY)\s?\d[\d,]*(?:\.\d{2})?"  # USD 20.00
    r"|\d[\d,]*\.\d{2}\s?(?:USD|EUR|GBP|CAD|AUD)\b",  # 20.00 USD
    re.IGNORECASE,
)
_RECURRING = re.compile(
    r"\b(?:subscri\w*|renew\w*|auto-renew\w*|membership|member|plan|trial|recurring"
    r"|monthly|yearly|annual\w*|per (?:month|year|week)|billing (?:period|date|cycle)"
    r"|next (?:billing|payment|charge)|cancel anytime)\b"
    r"|/\s?(?:mo|month|yr|year)\b",
    re.IGNORECASE,
)


def looks_like_subscription(subject: str, text: str) -> bool:
    combined = f"{subject}\n{text}"
    return bool(_PRICE.search(combined) and _RECURRING.search(combined))
