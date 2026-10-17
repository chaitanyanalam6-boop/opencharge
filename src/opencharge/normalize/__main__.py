"""python -m opencharge.normalize --all | --hospital ID [ID ...]

Parse and normalize each hospital's most recently fetched file, then report what came out:
counts by price type, how many rates are NULL and why, payer match rate, and issues.
Nothing is loaded into price_line yet (that is Phase 3); this proves every format arrives
in one schema, and records each file's published date.
"""

import argparse
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import Engine, RowMapping, select, update

from opencharge.db import make_engine
from opencharge.models import Compression, source_file
from opencharge.normalize.payers import PayerMatcher
from opencharge.normalize.record import NormalizedPriceLine, normalize_line
from opencharge.normalize.text import parse_published_date
from opencharge.parse import get_parser
from opencharge.parse.records import PriceType, RawPriceLine, RejectedRow
from opencharge.registry import load_registry


@dataclass
class FileReport:
    hospital_id: str
    file_name: str
    published: str
    by_price_type: Counter[str] = field(default_factory=Counter)
    null_rates: Counter[str] = field(default_factory=Counter)
    issues: Counter[str] = field(default_factory=Counter)
    quirks: Counter[str] = field(default_factory=Counter)
    rejected: int = 0
    negotiated: int = 0
    negotiated_payer_matched: int = 0
    example: NormalizedPriceLine | None = None


def latest_files(engine: Engine, hospital_ids: list[str]) -> dict[str, RowMapping]:
    """The most recently fetched source_file row for each hospital that has one."""
    with engine.connect() as conn:
        rows = conn.execute(select(source_file).order_by(source_file.c.fetched_at)).mappings()
        latest = {r["hospital_id"]: r for r in rows}
    return {h: latest[h] for h in hospital_ids if h in latest}


def normalize_file(engine: Engine, file: RowMapping, payers: PayerMatcher) -> FileReport:
    hospital_id: str = file["hospital_id"]
    path = Path(file["local_path"])
    parser = get_parser(file["parser_name"], path, Compression(file["compression"]))
    header = parser.read_header()
    published = parse_published_date(header.last_updated_on)
    with engine.begin() as conn:
        conn.execute(
            update(source_file)
            .where(source_file.c.id == file["id"])
            .values(published_date=published)
        )

    report = FileReport(
        hospital_id,
        path.name[:12] + "…",
        f"{header.last_updated_on!r} -> {published}",
    )
    for item in parser.lines():
        if isinstance(item, RejectedRow):
            report.rejected += 1
            continue
        assert isinstance(item, RawPriceLine)
        line = normalize_line(item, hospital_id, payers)
        report.by_price_type[line.price_type.value] += 1
        report.issues.update(i.split(" (")[0] for i in line.issues)
        if line.rate_cents is None:
            reason = (
                "percentage only"
                if line.percentage
                else "algorithm only" if line.algorithm else "published as empty"
            )
            report.null_rates[reason] += 1
        if line.price_type is PriceType.NEGOTIATED:
            report.negotiated += 1
            report.negotiated_payer_matched += line.payer_id is not None
            if report.example is None and line.payer_id and line.code_type:
                report.example = line
    report.quirks = parser.quirks
    return report


def print_report(r: FileReport) -> None:
    total = sum(r.by_price_type.values())
    print(f"\n== {r.hospital_id}  ({r.file_name}, last_updated_on {r.published})")
    print(
        f"   price lines: {total:,}  "
        + "  ".join(f"{k}={v:,}" for k, v in r.by_price_type.most_common())
    )
    print(f"   rejected rows: {r.rejected:,}")
    print(
        "   NULL rate_cents: "
        + (", ".join(f"{k}={v:,}" for k, v in r.null_rates.most_common()) or "none")
    )
    if r.negotiated:
        share = r.negotiated_payer_matched / r.negotiated
        matched = f"{r.negotiated_payer_matched:,}/{r.negotiated:,} ({share:.1%})"
        print(f"   negotiated lines with a canonical payer: {matched}")
    print("   issues: " + (", ".join(f"{k}={v:,}" for k, v in r.issues.most_common(8)) or "none"))
    print(
        "   parser quirks: "
        + (", ".join(f"{k}={v:,}" for k, v in r.quirks.most_common()) or "none")
    )
    if r.example:
        e = r.example
        print(
            f"   example: {e.code_type} {e.code} {e.description_clean[:40]!r} {e.setting} "
            f"{e.payer_id} rate_cents={e.rate_cents} pct={e.percentage} "
            f"<- row {e.source_row_number}, {e.source_locator[:60]}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m opencharge.normalize", description=__doc__)
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--all", action="store_true")
    which.add_argument("--hospital", nargs="+", metavar="ID")
    args = parser.parse_args(argv)

    registry = load_registry()
    ids = [h.id for h in registry.hospitals] if args.all else args.hospital
    engine = make_engine()
    files = latest_files(engine, ids)
    for missing in sorted(set(ids) - set(files)):
        print(
            f"{missing}: no fetched file yet; run python -m opencharge.ingest first",
            file=sys.stderr,
        )

    payers = PayerMatcher()
    for file in files.values():
        print_report(normalize_file(engine, file, payers))
    review = payers.write_review_file()
    print(f"\nUnmatched payer names ({len(payers.unmatched)}) written to {review}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
