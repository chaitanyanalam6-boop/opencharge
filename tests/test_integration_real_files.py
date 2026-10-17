"""Parse the real hospital files in data/raw/. Run with:  pytest -m integration

Skipped in CI (the files are hundreds of MB and not in git) and skipped locally for any
hospital not fetched yet.
"""

from itertools import islice
from pathlib import Path

import pytest

from opencharge import PROJECT_ROOT
from opencharge.normalize.payers import PayerMatcher
from opencharge.normalize.record import normalize_line
from opencharge.parse import get_parser
from opencharge.parse.json_v3 import text
from opencharge.parse.provenance import raw_csv_row, raw_json_item, resolve_json_pointer
from opencharge.parse.records import RawPriceLine, RejectedRow
from opencharge.registry import FileFormat, load_registry

pytestmark = pytest.mark.integration

ENTRIES = load_registry().hospitals


def raw_file(hospital_id: str) -> Path:
    files = sorted((PROJECT_ROOT / "data" / "raw" / hospital_id).glob("[0-9a-f]*"))
    if not files:
        pytest.skip(f"{hospital_id} not fetched; run python -m opencharge.ingest")
    return files[-1]


@pytest.mark.parametrize("entry", ENTRIES, ids=[e.id for e in ENTRIES])
def test_real_file_parses_and_first_prices_trace_back_to_source(entry) -> None:  # type: ignore[no-untyped-def]
    path = raw_file(entry.id)
    parser = get_parser(entry.parser, path)
    assert parser.read_header().version is not None

    first = list(islice(parser.lines(), 200))
    lines = [x for x in first if isinstance(x, RawPriceLine)]
    assert lines, "no price lines in the first 200 records"
    assert not [x for x in first if isinstance(x, RejectedRow)]

    for line in lines[:25]:
        if entry.format is FileFormat.JSON:
            item = raw_json_item(path, line.source_row_number)
            value = text(resolve_json_pointer(item, line.source_locator))
        else:
            value = raw_csv_row(path, line.source_row_number)[line.source_locator].strip()
        assert value in (line.dollar, line.percentage, line.algorithm)

    payers = PayerMatcher()
    for line in lines:
        normalized = normalize_line(line, entry.id, payers)
        assert normalized.source_row_number == line.source_row_number
