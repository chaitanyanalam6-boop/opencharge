"""Opening and closing ingest_run rows around processing."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Engine, select, update

from opencharge.load.pipeline import FileResult
from opencharge.models import RunStatus, ingest_run


def start_run(engine: Engine) -> str:
    run_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            ingest_run.insert().values(
                id=run_id, started_at=datetime.now(UTC), status=RunStatus.RUNNING.value
            )
        )
    return run_id


def finish_run(engine: Engine, run_id: str, results: list[FileResult]) -> RunStatus:
    """Record row counts and the final status. Fetch failures outrank drift."""
    with engine.begin() as conn:
        current = conn.scalar(select(ingest_run.c.status).where(ingest_run.c.id == run_id))
        notes = conn.scalar(select(ingest_run.c.notes).where(ingest_run.c.id == run_id))
        drifted = [r.hospital_id for r in results if r.drifted]
        if current == RunStatus.FAILED:
            status = RunStatus.FAILED
        elif drifted:
            status = RunStatus.DRIFT
        else:
            status = RunStatus.SUCCESS
        processed = f"processed {len(results)} file(s)" + (
            f"; drift: {', '.join(drifted)}" if drifted else ""
        )
        conn.execute(
            update(ingest_run)
            .where(ingest_run.c.id == run_id)
            .values(
                finished_at=datetime.now(UTC),
                status=status.value,
                rows_in=sum(r.rows_in for r in results),
                rows_loaded=sum(r.rows_loaded for r in results),
                rows_quarantined=sum(r.rows_quarantined for r in results),
                notes=f"{notes}; {processed}" if notes else processed,
            )
        )
    return status


def format_results(results: list[FileResult]) -> str:
    if not results:
        return "No files needed processing (all unchanged)."
    lines = [
        f"{'hospital':40} {'rows in':>11} {'loaded':>11} {'quarantined':>12}  top rules",
        "-" * 100,
    ]
    for r in results:
        rules = ", ".join(f"{k}={v:,}" for k, v in r.quarantined_by_rule.most_common(3))
        flag = "  DRIFT" if r.drifted else ""
        lines.append(
            f"{r.hospital_id:40} {r.rows_in:>11,} {r.rows_loaded:>11,} "
            f"{r.rows_quarantined:>12,}  {rules}{flag}"
        )
    return "\n".join(lines)
