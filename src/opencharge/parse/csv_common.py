"""Shared reading logic for the two CMS v3 CSV templates.

Both templates start the same way:
  row 1  metadata keys    (hospital_name, last_updated_on, version, ...)
  row 2  metadata values
  row 3  column header
  row 4+ data
"""

import csv
import re
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

from opencharge.models import Compression
from opencharge.parse._io import decoded_lines, open_binary
from opencharge.parse.records import Code, FileHeader, PriceType

FIRST_DATA_ROW = 4

# Item-level price columns, identical in tall and wide.
ITEM_PRICE_COLUMNS = {
    "standard_charge|gross": PriceType.GROSS,
    "standard_charge|discounted_cash": PriceType.CASH,
    "standard_charge|min": PriceType.MIN,
    "standard_charge|max": PriceType.MAX,
}

_CODE_COLUMN = re.compile(r"^code\|(\d+)$")


# Template keywords that can end a column name. Only these are lowercased: the last segment
# of "additional_payer_notes|Aetna|PPO" is a plan name and must keep its spelling.
_STRUCTURAL_SUFFIXES = frozenset(
    {
        "type",
        "gross",
        "discounted_cash",
        "min",
        "max",
        "negotiated_dollar",
        "negotiated_percentage",
        "negotiated_algorithm",
        "methodology",
    }
)


def normalize_column(name: str) -> str:
    """'code | 1 ' -> 'code|1'. CMS's own sample has spaces around pipes; GBMC has trailing ones.

    Whitespace around pipes is removed and template keywords are lowercased; payer and plan
    names in wide headers keep their spelling.
    """
    parts = [part.strip() for part in name.replace("﻿", "").split("|")]
    parts[0] = parts[0].lower()
    if len(parts) > 1 and parts[-1].lower() in _STRUCTURAL_SUFFIXES:
        parts[-1] = parts[-1].lower()
    return "|".join(parts)


class Row:
    """One data row, addressable by normalized column name."""

    def __init__(self, number: int, cells: list[str], columns: dict[str, int]) -> None:
        self.number = number
        self.cells = cells
        self.columns = columns

    @property
    def aligned(self) -> bool:
        return len(self.cells) == len(self.columns)

    @property
    def is_modifier_rule(self) -> bool:
        """A row with modifiers but no item code ("Bilateral procedure, modifier 50: 150%").

        These are payment adjustments applied to other items, not prices for a service.
        The JSON template keeps them in a separate modifier_information section; the CSV
        templates mix them into the item rows. We skip them in every format.
        """
        return self.get("modifiers") is not None and not self.codes()

    def at(self, index: int | None) -> str | None:
        """Stripped cell value, or None when the column is missing or the cell is blank."""
        if index is None:
            return None
        value = self.cells[index].strip()
        return value or None

    def get(self, column: str) -> str | None:
        return self.at(self.columns.get(column))

    def codes(self) -> tuple[Code, ...]:
        """All code|N / code|N|type pairs with a code present, in N order."""
        found = []
        for name, index in self.columns.items():
            match = _CODE_COLUMN.match(name)
            code = self.at(index) if match else None
            if match and code is not None:
                code_type = self.get(f"code|{match.group(1)}|type") or ""
                found.append((int(match.group(1)), Code(code, code_type)))
        return tuple(code for _, code in sorted(found))

    def item_fields(self) -> dict[str, object]:
        """The fields that describe the item itself, shared by every price on the row."""
        return {
            "description": self.get("description") or "",
            "codes": self.codes(),
            "setting": self.get("setting"),
            "billing_class": self.get("billing_class"),
            "modifiers": self.get("modifiers"),
            "drug_unit": self.get("drug_unit_of_measurement"),
            "drug_type": self.get("drug_type_of_measurement"),
        }


class CsvTemplate:
    """Opens a CMS v3 CSV and exposes its header and data rows with spreadsheet row numbers."""

    def __init__(self, path: Path, compression: Compression = Compression.NONE) -> None:
        self.path = path
        self.compression = compression
        self.quirks: Counter[str] = Counter()

    def read_header(self) -> FileHeader:
        with open_binary(self.path, self.compression) as stream:
            reader = csv.reader(decoded_lines(stream, Counter()))
            keys = [normalize_column(k) for k in next(reader, [])]
            values = next(reader, [])
        meta = {k: v.strip() or None for k, v in zip(keys, values, strict=False)}
        return FileHeader(
            hospital_name=meta.get("hospital_name"),
            last_updated_on=meta.get("last_updated_on"),
            version=meta.get("version"),
            location_name=meta.get("location_name"),
            hospital_address=meta.get("hospital_address"),
        )

    def rows(self) -> Iterator[Row]:
        """Yield each data row.

        Row numbers count CSV records, not physical lines, so a description containing a
        line break still counts as one row, matching what a spreadsheet shows.
        """
        with open_binary(self.path, self.compression) as stream:
            reader = csv.reader(decoded_lines(stream, self.quirks))
            next(reader, None)
            next(reader, None)
            header = [normalize_column(c) for c in next(reader, [])]
            columns = {name: i for i, name in enumerate(header)}
            for number, cells in enumerate(reader, start=FIRST_DATA_ROW):
                yield Row(number, cells, columns)
