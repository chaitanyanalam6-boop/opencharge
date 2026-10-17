"""CMS v3 CSV "tall": one row per item per payer/plan.

Columns payer_name, plan_name, standard_charge|negotiated_* hold one payer's rate. The
item-level prices (gross, cash, min, max) are repeated on every payer row for that item;
see parse/dedupe.py for how those repeats are collapsed.
"""

from collections.abc import Iterator

from opencharge.parse.csv_common import ITEM_PRICE_COLUMNS, CsvTemplate, Row
from opencharge.parse.dedupe import FirstSeen
from opencharge.parse.records import FileHeader, PriceType, RawPriceLine, RejectedRow

_NEGOTIATED_COLUMNS = (
    "standard_charge|negotiated_dollar",
    "standard_charge|negotiated_percentage",
    "standard_charge|negotiated_algorithm",
)


def misaligned(row: Row) -> RejectedRow:
    return RejectedRow(
        row.number,
        f"row has {len(row.cells)} cells, header has {len(row.columns)}",
        tuple(row.cells),
    )


class CsvTallParser:
    name = "cms_csv_tall_v3"

    def __init__(self, template: CsvTemplate) -> None:
        self.template = template
        self.quirks = template.quirks

    def read_header(self) -> FileHeader:
        return self.template.read_header()

    def schema_columns(self) -> list[str] | None:
        return self.template.header_columns()

    def lines(self) -> Iterator[RawPriceLine | RejectedRow]:
        first_seen = FirstSeen(self.quirks)

        for row in self.template.rows():
            if not row.aligned:
                yield misaligned(row)
                continue
            if row.is_modifier_rule:
                self.quirks["modifier_rule_rows_skipped"] += 1
                continue
            item = row.item_fields()

            for column, price_type in ITEM_PRICE_COLUMNS.items():
                value = row.get(column)
                if value is None or not first_seen.is_first(item, price_type, value):
                    continue
                yield RawPriceLine(
                    source_row_number=row.number,
                    source_locator=column,
                    price_type=price_type,
                    payer_name=None,
                    plan_name=None,
                    dollar=value,
                    percentage=None,
                    algorithm=None,
                    methodology=None,
                    notes=row.get("additional_generic_notes"),
                    **item,  # type: ignore[arg-type]
                )

            dollar, percentage, algorithm = (row.get(c) for c in _NEGOTIATED_COLUMNS)
            payer, plan = row.get("payer_name"), row.get("plan_name")
            if dollar is None and percentage is None and algorithm is None:
                if payer is not None or plan is not None:
                    # A payer named with no rate of any kind: worth counting, not a price.
                    self.quirks["tall_payer_row_without_rate"] += 1
                continue
            locator = next(c for c in _NEGOTIATED_COLUMNS if row.get(c) is not None)
            yield RawPriceLine(
                source_row_number=row.number,
                source_locator=locator,
                price_type=PriceType.NEGOTIATED,
                payer_name=payer,
                plan_name=plan,
                dollar=dollar,
                percentage=percentage,
                algorithm=algorithm,
                methodology=row.get("standard_charge|methodology"),
                notes=row.get("additional_generic_notes"),
                **item,  # type: ignore[arg-type]
            )
