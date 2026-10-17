"""Turn published money strings into integer cents.

Money is never a float. Values are parsed with Decimal and converted to integer cents.
A value that says "there is no price" ("N/A", "-") becomes None, never 0: the UI must be
able to tell "free" from "not published".
"""

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# Lowercased strings hospitals use to mean "no value here".
NULL_TOKENS = frozenset(
    {"n/a", "na", "n.a.", "-", "--", "—", "null", "none", "nan", "not applicable", "tbd", "."}
)
_NOISE = re.compile(r"[$,\s]|usd", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Money:
    cents: int | None
    # Why the value isn't a clean amount, if it isn't. None when it parsed cleanly or was absent.
    issue: str | None = None


def parse_money(raw: str | None) -> Money:
    if raw is None:
        return Money(None)
    text = raw.strip()
    if text.lower() in NULL_TOKENS or not text:
        return Money(None, "published_as_empty")

    negative = text.startswith("(") and text.endswith(")")  # accounting style: (12.50)
    cleaned = _NOISE.sub("", text.strip("()"))
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return Money(None, "unparseable_amount")
    if not amount.is_finite():
        return Money(None, "unparseable_amount")
    if negative:
        amount = -amount

    cents = (amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    # MedStar publishes cash prices like 3793.7662: storing whole cents means rounding, which
    # we do once, half-up, and flag. The raw string is kept alongside for provenance.
    issue = "sub_cent_rounded" if cents != amount * 100 else None
    return Money(int(cents), issue)


def parse_percentage(raw: str | None) -> Decimal | None:
    """'92.3' or '92.3%' -> Decimal('92.3'). Unparseable -> None."""
    if raw is None:
        return None
    try:
        value = Decimal(raw.strip().rstrip("%").strip())
    except InvalidOperation:
        return None
    return value if value.is_finite() else None
