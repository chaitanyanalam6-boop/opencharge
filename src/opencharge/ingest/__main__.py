"""python -m opencharge.ingest --all | --hospital ID [ID ...]"""

import argparse
import logging
import os
import sys

from opencharge.db import make_engine
from opencharge.ingest.fetch import RunSummary, run_fetch
from opencharge.ingest.polite import DEFAULT_MIN_INTERVAL_SECONDS, PoliteClient, build_http_client
from opencharge.registry import load_registry


def format_summary(summary: RunSummary) -> str:
    lines = [
        f"Run {summary.run_id}: {summary.status.value.upper()}",
        "",
        f"{'hospital':32} {'outcome':10} {'size':>12}  detail",
        "-" * 72,
    ]
    for r in summary.results:
        size = f"{r.byte_size / 1_000_000:,.1f} MB" if r.byte_size is not None else "-"
        lines.append(f"{r.hospital_id:32} {r.outcome.value:10} {size:>12}  {r.error or ''}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m opencharge.ingest", description=__doc__)
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--all", action="store_true", help="fetch every registry hospital")
    which.add_argument("--hospital", nargs="+", metavar="ID", help="fetch only these ids")
    parser.add_argument(
        "--min-interval",
        type=float,
        default=DEFAULT_MIN_INTERVAL_SECONDS,
        help="seconds between requests to the same host",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    contact = os.environ.get("OPENCHARGE_CONTACT_EMAIL", "")
    if "@" not in contact:
        print(
            "OPENCHARGE_CONTACT_EMAIL is not set. Hospitals must be able to reach us; "
            "copy .env.example to .env, fill it in, and run with `uv run --env-file .env ...`.",
            file=sys.stderr,
        )
        return 2

    with build_http_client(contact) as client:
        summary = run_fetch(
            make_engine(),
            load_registry(),
            PoliteClient(client, min_interval=args.min_interval),
            hospital_ids=None if args.all else args.hospital,
        )
    print(format_summary(summary))
    return 0 if summary.status.value == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
