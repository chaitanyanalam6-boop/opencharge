"""cms-hpt.txt parsing. Each fixture reproduces a quirk observed in a real MD/DC index."""

import httpx
import pytest

from opencharge.ingest.hpt import (
    candidate_registry_entry,
    fetch_cms_hpt,
    index_url_for,
    parse_cms_hpt,
)

CLEAN = """location-name: Hospital A
source-page-url: https://example.org/prices
mrf-url: https://example.org/111_hospital-a_standardcharges.csv
contact-name: Pat Doe
contact-email: pat@example.org

location-name: Hospital B
source-page-url: https://example.org/prices
mrf-url: https://example.org/222_hospital-b_standardcharges.json
contact-name: Pat Doe
contact-email: pat@example.org
"""


def test_clean_file_gives_one_entry_per_block() -> None:
    entries = parse_cms_hpt(CLEAN)
    assert [e.location_name for e in entries] == ["Hospital A", "Hospital B"]
    assert entries[1].mrf_url == "https://example.org/222_hospital-b_standardcharges.json"
    assert all(e.quirks == [] for e in entries)


def test_bom_and_crlf_are_handled() -> None:
    # Seen in Mercy Medical Center's index.
    entries = parse_cms_hpt("﻿" + CLEAN.replace("\n", "\r\n"))
    assert entries[0].location_name == "Hospital A"
    assert entries[0].mrf_url == "https://example.org/111_hospital-a_standardcharges.csv"


def test_mfr_url_misspelling_is_read_and_reported() -> None:
    # Seen in Holy Cross Health's index.
    text = "location-name: Hospital A\nmfr-url: https://example.org/a.csv\n"
    [entry] = parse_cms_hpt(text)
    assert entry.mrf_url == "https://example.org/a.csv"
    assert any("mfr-url" in q for q in entry.quirks)


def test_trailing_whitespace_is_stripped_from_urls() -> None:
    # Seen in Frederick Health's and Novant's indexes.
    [entry] = parse_cms_hpt("location-name: A\nmrf-url: https://example.org/a.json   \n")
    assert entry.mrf_url == "https://example.org/a.json"


def test_duplicate_blocks_are_collapsed() -> None:
    # Seen in ChristianaCare's index: three identical blocks.
    block = "location-name: A\nmrf-url: https://example.org/a.csv\n\n"
    assert len(parse_cms_hpt(block * 3)) == 1


def test_blocks_without_blank_line_between_are_split() -> None:
    text = (
        "location-name: A\nmrf-url: https://example.org/a.csv\n"
        "location-name: B\nmrf-url: https://example.org/b.csv\n"
    )
    assert [e.location_name for e in parse_cms_hpt(text)] == ["A", "B"]


def test_unknown_keys_and_garbage_lines_are_reported_not_dropped_silently() -> None:
    text = "location-name: A\nwebsite: https://example.org\nthis line has no separator\n"
    [entry] = parse_cms_hpt(text)
    assert len(entry.quirks) == 2


def test_empty_file_gives_no_entries() -> None:
    assert parse_cms_hpt("") == []
    assert parse_cms_hpt("﻿\r\n\r\n") == []


@pytest.mark.parametrize(
    "domain",
    ["medstarhealth.org", "medstarhealth.org/", "https://medstarhealth.org/some/page"],
)
def test_index_url_for(domain: str) -> None:
    assert index_url_for(domain) == "https://medstarhealth.org/cms-hpt.txt"


def test_fetch_cms_hpt_uses_mock_transport() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/cms-hpt.txt"
        return httpx.Response(200, text=CLEAN)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        entries = fetch_cms_hpt(client, "example.org")
    assert len(entries) == 2


def test_fetch_cms_hpt_raises_on_404() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(404))
    with httpx.Client(transport=transport) as client, pytest.raises(httpx.HTTPStatusError):
        fetch_cms_hpt(client, "example.org")


def test_candidate_entry_leaves_unknowable_fields_empty() -> None:
    entries = parse_cms_hpt(CLEAN)
    csv_draft = candidate_registry_entry(entries[0], "https://example.org/cms-hpt.txt")
    json_draft = candidate_registry_entry(entries[1], "https://example.org/cms-hpt.txt")

    assert csv_draft["id"] == "hospital-a"
    # A .csv URL doesn't say wide or tall, so the format is left for a person to decide.
    assert csv_draft["format"] is None
    assert json_draft["format"] == "json"
    for unknowable in ("ccn", "address", "lat", "lon", "parser"):
        assert csv_draft[unknowable] is None
