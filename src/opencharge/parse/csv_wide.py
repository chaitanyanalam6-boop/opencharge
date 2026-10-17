"""CMS v3 CSV "wide": one row per item, one block of columns per payer/plan.

Payer columns are named  standard_charge|<payer>|<plan>|negotiated_dollar  (and
_percentage, _algorithm, |methodology), plus  additional_payer_notes|<payer>|<plan>.
The payer and plan names live only in the column header, so they are read from there.
"""

from collections.abc import Iterator
from dataclasses import dataclass

from opencharge.parse.csv_common import ITEM_PRICE_COLUMNS, CsvTemplate
from opencharge.parse.csv_tall import misaligned
from opencharge.parse.dedupe import FirstSeen
from opencharge.parse.records import FileHeader, PriceType, RawPriceLine, RejectedRow

_PAYER_FIELDS = {
    "negotiated_dollar": "dollar",
    "negotiated_percentage": "percentage",
    "negotiated_algorithm": "algorithm",
    "methodology": "methodology",
}


@dataclass
class PayerColumns:
    """Column positions of one payer/plan block."""

    payer: str
    plan: str
    dollar: int | None = None
    percentage: int | None = None
    algorithm: int | None = None
    methodology: int | None = None
    notes: int | None = None


def payer_column_groups(columns: dict[str, int]) -> list[PayerColumns]:
    """Find every payer/plan block in a wide header, in column order."""
    groups: dict[tuple[str, str], PayerColumns] = {}
    for name, index in columns.items():
        parts = name.split("|")
        if parts[0] == "standard_charge" and len(parts) == 4 and parts[3] in _PAYER_FIELDS:
            group = groups.setdefault((parts[1], parts[2]), PayerColumns(parts[1], parts[2]))
            setattr(group, _PAYER_FIELDS[parts[3]], index)
        elif parts[0] == "additional_payer_notes" and len(parts) == 3:
            group = groups.setdefault((parts[1], parts[2]), PayerColumns(parts[1], parts[2]))
            group.notes = index
    return list(groups.values())


class CsvWideParser:
    name = "cms_csv_wide_v3"

    def __init__(self, template: CsvTemplate) -> None:
        self.template = template
        self.quirks = template.quirks

    def read_header(self) -> FileHeader:
        return self.template.read_header()

    def lines(self) -> Iterator[RawPriceLine | RejectedRow]:
        groups: list[PayerColumns] | None = None
        column_names: dict[int, str] = {}
        first_seen = FirstSeen(self.quirks)

        for row in self.template.rows():
            if groups is None:
                groups = payer_column_groups(row.columns)
                column_names = {i: name for name, i in row.columns.items()}
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

            for g in groups:
                rate_columns = [
                    i for i in (g.dollar, g.percentage, g.algorithm) if i is not None and row.at(i)
                ]
                if not rate_columns:
                    continue  # this payer publishes nothing for this item
                yield RawPriceLine(
                    source_row_number=row.number,
                    source_locator=column_names[rate_columns[0]],
                    price_type=PriceType.NEGOTIATED,
                    payer_name=g.payer,
                    plan_name=g.plan,
                    dollar=row.at(g.dollar),
                    percentage=row.at(g.percentage),
                    algorithm=row.at(g.algorithm),
                    methodology=row.at(g.methodology),
                    notes=row.at(g.notes),
                    **item,  # type: ignore[arg-type]
                )
