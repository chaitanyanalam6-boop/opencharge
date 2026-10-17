"""Small text cleanups: descriptions and published dates."""

import re
from datetime import date, datetime

_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%Y/%m/%d")


def clean_description(raw: str) -> str:
    """Whitespace collapsed and CSV-style doubled quotes undone; wording and case untouched.

    Novant writes '""Y SET"" TUBING': quotes escaped for CSV, then escaped again for JSON.
    """
    text = raw.replace('""', '"')
    return re.sub(r"\s+", " ", text).strip()


def parse_published_date(raw: str | None) -> date | None:
    """The file's last_updated_on as a date, or None if absent or in a format we don't know.

    Hospitals write this many ways ('1/1/2026', '2026-04-01'). An unknown format is a gap
    to report, not a date to guess.
    """
    if not raw:
        return None
    text = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None
