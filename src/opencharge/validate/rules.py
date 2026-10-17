"""Row-level validation rules. A line that fails any rule is quarantined, never loaded.

Each rule has a stable name; quarantined_row.failed_rule records the first one a line
fails, and `detail` lists all of them. The names come from the brief so the quarantine
table, the status page and the README all speak the same language.
"""

from collections.abc import Callable
from dataclasses import dataclass

from opencharge.normalize.record import NormalizedPriceLine
from opencharge.parse.records import PriceType

# Anything above this is almost certainly a data error (e.g. cents written as dollars).
MAX_SANE_RATE_CENTS = 5_000_000 * 100

# Rule names that do not come from checking a single normalized line.
ROW_MISALIGNED = "row_misaligned"  # the parser could not read the row safely
SCHEMA_DRIFT = "schema_drift"  # the value sits in a column the previous file did not have


@dataclass(frozen=True)
class Rule:
    name: str
    description: str
    passes: Callable[[NormalizedPriceLine], bool]


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
        lambda line: line.rate_cents is None or line.rate_cents <= MAX_SANE_RATE_CENTS,
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


def failed_rules(line: NormalizedPriceLine) -> list[str]:
    """Names of every rule the line fails, in rule order. Empty means the line is valid."""
    return [rule.name for rule in RULES if not rule.passes(line)]
