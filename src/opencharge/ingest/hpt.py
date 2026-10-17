"""Discover hospital files from a health system's /cms-hpt.txt index.

CMS asks every hospital to publish https://<domain>/cms-hpt.txt: blocks of `key: value`
lines, one block per facility, separated by blank lines. Real files bend that format. The
parser below was written against what we actually found in Maryland and DC indexes:

- a UTF-8 byte-order mark before the first key
- Windows (CRLF) line endings
- the key misspelled as `mfr-url` instead of `mrf-url`
- trailing spaces after URLs
- the same facility listed more than once
- blocks with no blank line between them

Anything we can't interpret is reported on the entry as a quirk, never silently fixed.
"""

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

# Misspellings seen in the wild, mapped to the key CMS specifies.
KEY_ALIASES = {"mfr-url": "mrf-url"}
KNOWN_KEYS = {"location-name", "source-page-url", "mrf-url", "contact-name", "contact-email"}


@dataclass
class HptEntry:
    location_name: str | None = None
    source_page_url: str | None = None
    mrf_url: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    # Human-readable notes about anything non-standard in this block.
    quirks: list[str] = field(default_factory=list)

    def _set(self, key: str, value: str) -> None:
        setattr(self, key.replace("-", "_"), value)

    def _has(self, key: str) -> bool:
        return getattr(self, key.replace("-", "_")) is not None


def parse_cms_hpt(text: str) -> list[HptEntry]:
    """Parse the text of a cms-hpt.txt file into one entry per facility."""
    text = text.lstrip("﻿")
    entries: list[HptEntry] = []
    current = HptEntry()

    def flush() -> None:
        nonlocal current
        if any(current._has(k) for k in KNOWN_KEYS):
            entries.append(current)
        current = HptEntry()

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            flush()
            continue
        if ":" not in line:
            current.quirks.append(f"line {line_number}: no 'key: value' separator, ignored")
            continue

        raw_key, value = line.split(":", 1)
        key = raw_key.strip().lower()
        value = value.strip()
        if key in KEY_ALIASES:
            current.quirks.append(
                f"line {line_number}: key '{raw_key}' read as '{KEY_ALIASES[key]}'"
            )
            key = KEY_ALIASES[key]
        if key not in KNOWN_KEYS:
            current.quirks.append(f"line {line_number}: unknown key '{raw_key}', ignored")
            continue

        # A repeated key means a new facility started without a blank line between blocks.
        if current._has(key):
            flush()
        current._set(key, value)

    flush()
    return _dedupe(entries)


def _dedupe(entries: list[HptEntry]) -> list[HptEntry]:
    seen: set[tuple[str | None, str | None]] = set()
    unique: list[HptEntry] = []
    for entry in entries:
        identity = (entry.location_name, entry.mrf_url)
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(entry)
    return unique


def index_url_for(domain: str) -> str:
    """'medstarhealth.org' or 'https://www.medstarhealth.org/x' -> its cms-hpt.txt URL."""
    host = urlparse(domain).netloc if "://" in domain else domain.strip("/")
    return f"https://{host}/cms-hpt.txt"


def fetch_cms_hpt(client: httpx.Client, domain: str) -> list[HptEntry]:
    """Fetch and parse a domain's cms-hpt.txt. Raises httpx.HTTPStatusError on 4xx/5xx."""
    response = client.get(index_url_for(domain))
    response.raise_for_status()
    return parse_cms_hpt(response.text)


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def candidate_registry_entry(entry: HptEntry, index_url: str) -> dict[str, object]:
    """Draft a hospitals.yaml entry from an index block.

    Fields the index can't tell us (CCN, address, coordinates, CSV shape, parser) are left
    as None. The registry loader rejects None for those fields, so a draft can't be loaded
    until a person fills them in from a primary source.
    """
    path = urlparse(entry.mrf_url or "").path.lower()
    # Only JSON is knowable from the URL. A .csv may be wide or tall; that needs a look inside.
    file_format = "json" if path.endswith(".json") else None
    return {
        "id": slugify(entry.location_name or ""),
        "name": entry.location_name,
        "ccn": None,
        "address": None,
        "lat": None,
        "lon": None,
        "type": None,
        "in_service_area": True,
        "format": file_format,
        "parser": None,
        "mrf_url": entry.mrf_url,
        "discovery": {
            "method": "cms_hpt_txt",
            "index_url": index_url,
            "source_page_url": entry.source_page_url,
        },
    }
