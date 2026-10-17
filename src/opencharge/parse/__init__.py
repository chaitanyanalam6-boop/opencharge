"""One parser per CMS file shape, all yielding RawPriceLine (or RejectedRow).

Parsers are looked up by the `parser` id recorded in sources/hospitals.yaml.
"""

from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

from opencharge.models import Compression
from opencharge.parse.csv_common import CsvTemplate
from opencharge.parse.csv_tall import CsvTallParser
from opencharge.parse.csv_wide import CsvWideParser
from opencharge.parse.json_v3 import JsonV3Parser
from opencharge.parse.records import FileHeader, RawPriceLine, RejectedRow


class Parser(Protocol):
    name: str
    quirks: Counter[str]

    def read_header(self) -> FileHeader: ...

    def lines(self) -> Iterator[RawPriceLine | RejectedRow]: ...

    def schema_columns(self) -> list[str] | None:
        """Column names (CSV) or key paths (JSON) for drift detection; None if not yet known."""
        ...


def get_parser(name: str, path: Path, compression: Compression = Compression.NONE) -> Parser:
    if name == CsvTallParser.name:
        return CsvTallParser(CsvTemplate(path, compression))
    if name == CsvWideParser.name:
        return CsvWideParser(CsvTemplate(path, compression))
    if name == JsonV3Parser.name:
        return JsonV3Parser(path, compression)
    raise ValueError(f"unknown parser {name!r}")


PARSER_NAMES = (CsvTallParser.name, CsvWideParser.name, JsonV3Parser.name)
