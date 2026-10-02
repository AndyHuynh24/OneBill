"""Turn one typed or dictated phrase into a list of people: "Linh, Minh and Bao"."""

import re

MAX_NAME = 40

# Commas, semicolons, new lines, "&", "+", and the whole word "and" (so "Andy" survives).
_SEPARATORS = re.compile(r"[,;\n&+]|\band\b", re.IGNORECASE)


def parse_names(text: str, existing: list[str] = ()) -> list[str]:
    """Names in the order given, trimmed, without duplicates (case-insensitive) or `existing`."""
    seen = {name.casefold() for name in existing}
    names = []
    for part in _SEPARATORS.split(text or ""):
        name = " ".join(part.split())[:MAX_NAME].strip()
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            names.append(name)
    return names
