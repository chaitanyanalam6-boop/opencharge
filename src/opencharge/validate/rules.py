"""Row-level validation rules.

Each rule has a stable name (from the brief) and a severity:
  reject  the line is quarantined, never loaded. quarantined_row.failed_rule records the first
          rejecting rule a line fails, and `detail` lists all of them.
  flag    the line loads, carrying the rule name in its `issues`, so the UI can warn about it.
          Used where the value is suspicious but may be true: hiding a real price would be
          worse than showing it with a warning (see docs/DECISIONS.md).
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from opencharge.normalize.record import NormalizedPriceLine
from opencharge.parse.records import PriceType

# Above this a rate is suspicious (e.g. cents written as dollars), though not always wrong.
MAX_SANE_RATE_CENTS = 5_000_000 * 100

# Rule names that do not come from checking a single normalized line.
ROW_MISALIGNED = "row_misaligned"  # the parser could not read the row safely
SCHEMA_DRIFT = "schema_drift"  # the value sits in a column the previous file did not have


class Severity(StrEnum):
    REJECT = "reject"
    FLAG = "flag"


@dataclass(frozen=True)
class Rule:
    name: str
    description: str
    passes: Callable[[NormalizedPriceLine], bool]
    severity: Severity = Severity.REJECT


RULES: tuple[Rule, ...] = (
    Rule(
        "code_present",
        "The item has at least one real billing code.",
        lambda line: line.code is not None,
    ),
    Rule(
        "code_format_valid",
        "The searchable code has the shape its declared type requires.",
        lambda line: line.code_issue != "code_shape_mismatch",
    ),
    Rule(
        "rate_non_negative",
        "A dollar rate, if given, is not negative.",
        lambda line: line.rate_cents is None or line.rate_cents >= 0,
    ),
    Rule(
        "rate_within_sane_bounds",
        "A dollar rate, if given, is at most $5,000,000.",
        # Flagged, not rejected: Novant's Elevidys gene therapy is genuinely published at
        # $6.0-6.3M, and hiding a true price is worse than showing it with a warning.
        lambda line: line.rate_cents is None or line.rate_cents <= MAX_SANE_RATE_CENTS,
        Severity.FLAG,
    ),
    Rule(
        "price_type_known",
        "The price type is one of gross, cash, negotiated, min, max.",
        lambda line: line.price_type in PriceType,
    ),
    Rule(
        "payer_resolved_if_negotiated",
        "A negotiated rate names a payer we can map to a canonical payer.",
        lambda line: line.price_type is not PriceType.NEGOTIATED or line.payer_id is not None,
    ),
)

RULE_NAMES = tuple(rule.name for rule in RULES) + (ROW_MISALIGNED, SCHEMA_DRIFT)


def failed_rules(line: NormalizedPriceLine, severity: Severity = Severity.REJECT) -> list[str]:
    """Names of every rule of this severity the line fails, in rule order."""
    return [rule.name for rule in RULES if rule.severity is severity and not rule.passes(line)]
