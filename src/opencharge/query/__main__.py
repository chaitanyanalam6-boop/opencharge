"""python -m opencharge.query "knee MRI near Baltimore with Aetna"

Interpret a question with the LLM, show the structured search it produced, and (if it is
confident) run it. Needs GROQ_API_KEY: uv run --env-file .env python -m opencharge.query ...
"""

import argparse
import sys
from collections import defaultdict

from sqlalchemy import select

from opencharge.db import make_engine
from opencharge.models import payer
from opencharge.query.catalog import CodeCatalog
from opencharge.query.execute import execute
from opencharge.query.interpret import InterpretationError, interpret
from opencharge.query.llm import GroqClient


def dollars(cents: int | None) -> str:
    return f"${cents / 100:,.2f}" if cents is not None else "not published"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m opencharge.query", description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--yes", action="store_true", help="run the search even if unconfirmed")
    args = parser.parse_args(argv)

    engine = make_engine()
    with engine.connect() as conn:
        catalog = CodeCatalog.build(conn)
        payers = dict(conn.execute(select(payer.c.id, payer.c.canonical_name)).all())
    try:
        interpretation = interpret(args.question, GroqClient(), catalog, payers)
    except InterpretationError as error:
        print(error, file=sys.stderr)
        return 1

    print("Interpretation (the model only chose these filters):")
    for key, value in interpretation.model_dump().items():
        print(f"  {key} = {value}")
    if interpretation.needs_confirmation and not args.yes:
        print("\nNot confident enough to search. Confirm with --yes, or rephrase.")
        return 0

    with engine.connect() as conn:
        result = execute(conn, interpretation)
    by_hospital = defaultdict(list)
    for r in result.results:
        by_hospital[r.hospital_name].append(r)
    print(f"\n{result.hospitals_in_range} hospitals in range; {len(by_hospital)} with prices:")
    for name, rows in by_hospital.items():
        print(
            f"\n  {name}"
            + (f" ({rows[0].distance_miles} mi)" if rows[0].distance_miles is not None else "")
        )
        for r in rows[:6]:
            who = f"{r.payer_id} / {r.plan_name}" if r.payer_id else ""
            print(
                f"    {r.price_type:10} {dollars(r.rate_cents):>14}  {who[:40]:40} "
                f"row {r.provenance.row_number:,}  ({r.description[:30]})"
            )
        if len(rows) > 6:
            print(f"    ... {len(rows) - 6} more lines")
    for gap in result.gaps:
        print(f"\n  GAP {gap.hospital_name}: {gap.kind} — {gap.detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
