"""Go from a price's provenance (file + row number + locator) back to the raw source.

Used by tests to prove every parsed value really sits where its provenance says, and later
by the price-provenance screen to show the original row next to the normalized record.
This reads the file from the start, which is fine for single lookups; bulk lookups belong
in the database.
"""

import csv
from collections import Counter
from pathlib import Path
from typing import Any

import ijson

from opencharge.models import Compression
from opencharge.parse._io import decoded_lines, open_binary
from opencharge.parse.csv_common import FIRST_DATA_ROW, normalize_column
from opencharge.parse.json_v3 import ITEMS_PREFIX


def raw_csv_row(
    path: Path, row_number: int, compression: Compression = Compression.NONE
) -> dict[str, str]:
    """The data row a spreadsheet would show as `row_number`, keyed by normalized column."""
    if row_number < FIRST_DATA_ROW:
        raise ValueError(f"data rows start at {FIRST_DATA_ROW}")
    with open_binary(path, compression) as stream:
        reader = csv.reader(decoded_lines(stream, Counter()))
        next(reader)
        next(reader)
        header = [normalize_column(c) for c in next(reader)]
        for number, cells in enumerate(reader, start=FIRST_DATA_ROW):
            if number == row_number:
                return dict(zip(header, cells, strict=False))
    raise LookupError(f"{path.name} has no row {row_number}")


def raw_json_item(path: Path, item_number: int, compression: Compression = Compression.NONE) -> Any:
    """The `item_number`-th (1-based) entry of standard_charge_information."""
    with open_binary(path, compression) as stream:
        for number, item in enumerate(ijson.items(stream, ITEMS_PREFIX, use_float=False), start=1):
            if number == item_number:
                return item
    raise LookupError(f"{path.name} has no item {item_number}")


def resolve_json_pointer(item: Any, pointer: str) -> Any:
    """Follow the part of an RFC 6901 pointer below /standard_charge_information/<n>."""
    parts = pointer.strip("/").split("/")[2:]
    value = item
    for part in parts:
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value
