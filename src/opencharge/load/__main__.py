"""python -m opencharge.load --all | --hospital ID [ID ...] [--reprocess] [--accept-drift FILE_ID]

Process files already on disk, without fetching:
  (default)        load any fetched file that has not been processed yet
  --reprocess      reload each hospital's latest file, e.g. after a normalization rule changed
  --accept-drift   mark a file's column changes as reviewed, then reprocess so the lines
                   from those columns load instead of sitting in quarantine
"""

import argparse
import logging
import sys

from sqlalchemy import select

from opencharge.db import make_engine
from opencharge.load.pipeline import accept_drift, process_pending
from opencharge.load.runs import finish_run, format_results, start_run
from opencharge.models import RunStatus, source_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m opencharge.load", description=__doc__)
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--all", action="store_true")
    which.add_argument("--hospital", nargs="+", metavar="ID")
    which.add_argument("--accept-drift", metavar="FILE_ID")
    parser.add_argument("--reprocess", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    engine = make_engine()
    hospital_ids = args.hospital
    reprocess = args.reprocess
    if args.accept_drift:
        with engine.begin() as conn:
            accepted = accept_drift(conn, args.accept_drift)
            hospital_id = conn.scalar(
                select(source_file.c.hospital_id).where(source_file.c.id == args.accept_drift)
            )
        if not accepted or hospital_id is None:
            print(f"No unresolved drift for file {args.accept_drift}", file=sys.stderr)
            return 2
        hospital_ids, reprocess = [hospital_id], True

    run_id = start_run(engine)
    results = process_pending(engine, run_id, hospital_ids, reprocess=reprocess)
    status = finish_run(engine, run_id, results)
    print(format_results(results))
    print(f"\nRun {run_id}: {status.value.upper()}")
    return 0 if status is RunStatus.SUCCESS else 1


if __name__ == "__main__":
    sys.exit(main())
