"""Process one fetched file: parse -> normalize -> validate -> load or quarantine.

Everything for one file happens in one transaction: either all of its valid lines become
live and its older file is superseded, or nothing changes.

Schema drift: each file's columns (CSV header, or JSON key paths) are stored. If they differ
from the hospital's previous loaded file, a schema_drift row records what was added and
removed, the file and run are marked `drift`, and any price read from a column the previous
file did not have is quarantined (rule `schema_drift`) instead of being trusted. Everything
else in the file still loads.
"""

import dataclasses
import hashlib
import json
import logging
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, RowMapping, delete, select, update

from opencharge.load.bulk import insert_rows
from opencharge.models import (
    Compression,
    FileStatus,
    payer,
    price_line,
    quarantined_row,
    schema_drift,
    source_file,
)
from opencharge.normalize.payers import PayerMatcher
from opencharge.normalize.record import NormalizedPriceLine, normalize_line
from opencharge.normalize.text import parse_published_date
from opencharge.parse import get_parser
from opencharge.parse.records import RawPriceLine, RejectedRow
from opencharge.validate.rules import ROW_MISALIGNED, SCHEMA_DRIFT, Severity, failed_rules

log = logging.getLogger(__name__)

BATCH_SIZE = 50_000
UNKNOWN_SCHEMA_VERSION = "unknown"  # the file did not declare one; we don't guess


@dataclass
class FileResult:
    source_file_id: str
    hospital_id: str
    rows_in: int = 0
    rows_loaded: int = 0
    rows_quarantined: int = 0
    quarantined_by_rule: Counter[str] = field(default_factory=Counter)
    drift_added: list[str] = field(default_factory=list)
    drift_removed: list[str] = field(default_factory=list)

    @property
    def drifted(self) -> bool:
        return bool(self.drift_added or self.drift_removed)


def sync_payers(conn: Connection, matcher: PayerMatcher) -> None:
    """Make the payer table match payer_aliases.yaml."""
    existing = set(conn.scalars(select(payer.c.id)))
    for p in matcher.payers:
        values = {"canonical_name": p.name, "aliases": [x.pattern for x in p.patterns]}
        if p.id in existing:
            conn.execute(update(payer).where(payer.c.id == p.id).values(**values))
        else:
            conn.execute(payer.insert().values(id=p.id, **values))


def fingerprint(columns: list[str]) -> str:
    return hashlib.sha256("\n".join(columns).encode()).hexdigest()


def raw_payload(raw: RawPriceLine) -> dict[str, Any]:
    """The parsed line as the hospital wrote it, JSON-ready, for the quarantine table."""
    payload = asdict(raw)
    payload["price_type"] = raw.price_type.value
    return payload


def price_line_row(
    line: NormalizedPriceLine, file: RowMapping, schema_version: str, now: Any
) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "hospital_id": line.hospital_id,
        "code_type": line.code_type.value if line.code_type else None,
        "code": line.code,
        "other_codes": [[t.value, c] for t, c in line.other_codes] or None,
        "description_raw": line.description_raw,
        "description_clean": line.description_clean,
        "setting": line.setting.value,
        "price_type": line.price_type.value,
        "payer_id": line.payer_id,
        "payer_name_raw": line.payer_name_raw,
        "plan_name": line.plan_name,
        "rate_cents": line.rate_cents,
        "rate_raw": line.rate_raw,
        "percentage": line.percentage,
        "algorithm": line.algorithm,
        "methodology": line.methodology,
        "billing_class": line.billing_class,
        "modifiers": line.modifiers,
        "drug_unit": line.drug_unit,
        "drug_type": line.drug_type,
        "notes": line.notes,
        "issues": list(line.issues) or None,
        "source_file_id": file["id"],
        "source_row_number": line.source_row_number,
        "source_locator": line.source_locator,
        "parser_name": file["parser_name"],
        "schema_version": schema_version,
        "ingested_at": now,
    }


def previous_loaded_file(conn: Connection, file: RowMapping) -> RowMapping | None:
    """The hospital's most recent other file whose lines are (or were) live."""
    return (
        conn.execute(
            select(source_file)
            .where(
                source_file.c.hospital_id == file["hospital_id"],
                source_file.c.id != file["id"],
                source_file.c.status.in_(
                    [FileStatus.LOADED, FileStatus.DRIFT, FileStatus.SUPERSEDED]
                ),
                source_file.c.columns.is_not(None),
            )
            .order_by(source_file.c.fetched_at.desc())
            .limit(1)
        )
        .mappings()
        .first()
    )


def process_file(
    conn: Connection, file: RowMapping, run_id: str, payers: PayerMatcher
) -> FileResult:
    """Load one source file inside the caller's transaction."""
    result = FileResult(file["id"], file["hospital_id"])
    parser = get_parser(
        file["parser_name"], Path(file["local_path"]), Compression(file["compression"])
    )
    header = parser.read_header()
    schema_version = header.version or UNKNOWN_SCHEMA_VERSION
    now = datetime.now(UTC)

    # Reprocessing replaces this file's earlier output instead of duplicating it. Drift a
    # person has accepted is kept: those columns are now trusted.
    conn.execute(delete(price_line).where(price_line.c.source_file_id == file["id"]))
    conn.execute(delete(quarantined_row).where(quarantined_row.c.source_file_id == file["id"]))
    accepted: set[str] = set()
    for added in conn.scalars(
        select(schema_drift.c.added_columns).where(
            schema_drift.c.source_file_id == file["id"], schema_drift.c.resolved_at.is_not(None)
        )
    ):
        accepted.update(added)
    conn.execute(
        delete(schema_drift).where(
            schema_drift.c.source_file_id == file["id"], schema_drift.c.resolved_at.is_(None)
        )
    )

    previous = previous_loaded_file(conn, file)
    previous_columns = set(previous["columns"]) | accepted if previous else None
    columns = parser.schema_columns()  # CSV: known now. JSON: known after reading.
    new_columns = set(columns) - previous_columns if columns and previous_columns else set()

    loaded: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []

    def quarantine(row_number: int, locator: str | None, payload: Any, rules: list[str]) -> None:
        result.rows_quarantined += 1
        result.quarantined_by_rule[rules[0]] += 1
        quarantined.append(
            {
                "id": str(uuid.uuid4()),
                "source_file_id": file["id"],
                "ingest_run_id": run_id,
                "row_number": row_number,
                "source_locator": locator,
                "raw_payload": payload,
                "failed_rule": rules[0],
                "detail": ", ".join(rules) if len(rules) > 1 else None,
                "failed_at": now,
            }
        )

    def flush(force: bool = False) -> None:
        if force or len(loaded) >= BATCH_SIZE:
            insert_rows(conn, price_line, loaded)
            loaded.clear()
        if force or len(quarantined) >= BATCH_SIZE:
            insert_rows(conn, quarantined_row, quarantined)
            quarantined.clear()

    for item in parser.lines():
        result.rows_in += 1
        if isinstance(item, RejectedRow):
            quarantine(
                item.source_row_number,
                None,
                {"cells": list(item.raw_payload), "reason": item.reason},
                [ROW_MISALIGNED],
            )
        else:
            line = normalize_line(item, file["hospital_id"], payers)
            rules = failed_rules(line)
            if item.source_locator in new_columns:
                rules.insert(0, SCHEMA_DRIFT)
            if rules:
                payload = raw_payload(item) | {"normalization_issues": list(line.issues)}
                quarantine(item.source_row_number, item.source_locator, payload, rules)
            else:
                flags = failed_rules(line, Severity.FLAG)
                if flags:
                    line = dataclasses.replace(line, issues=line.issues + tuple(flags))
                loaded.append(price_line_row(line, file, schema_version, now))
                result.rows_loaded += 1
        flush()
    flush(force=True)

    columns = parser.schema_columns() or []
    if previous_columns is not None and set(columns) != previous_columns:
        result.drift_added = sorted(set(columns) - previous_columns)
        result.drift_removed = sorted(previous_columns - set(columns))
        conn.execute(
            schema_drift.insert().values(
                id=str(uuid.uuid4()),
                source_file_id=file["id"],
                previous_source_file_id=previous["id"],  # type: ignore[index]
                ingest_run_id=run_id,
                added_columns=result.drift_added,
                removed_columns=result.drift_removed,
                detected_at=now,
            )
        )

    # This file is now the hospital's live one: retire the lines of any older file.
    older = select(source_file.c.id).where(
        source_file.c.hospital_id == file["hospital_id"],
        source_file.c.id != file["id"],
        source_file.c.status.in_([FileStatus.LOADED, FileStatus.DRIFT]),
    )
    older_ids = list(conn.scalars(older))
    if older_ids:
        conn.execute(delete(price_line).where(price_line.c.source_file_id.in_(older_ids)))
        conn.execute(
            update(source_file)
            .where(source_file.c.id.in_(older_ids))
            .values(status=FileStatus.SUPERSEDED)
        )

    conn.execute(
        update(source_file)
        .where(source_file.c.id == file["id"])
        .values(
            status=FileStatus.DRIFT if result.drifted else FileStatus.LOADED,
            published_date=parse_published_date(header.last_updated_on),
            schema_version=schema_version,
            columns=columns,
            column_fingerprint=fingerprint(columns),
            rows_in=result.rows_in,
            rows_loaded=result.rows_loaded,
            rows_quarantined=result.rows_quarantined,
        )
    )
    log.info(
        "%s: %s in, %s loaded, %s quarantined %s%s",
        file["hospital_id"],
        f"{result.rows_in:,}",
        f"{result.rows_loaded:,}",
        f"{result.rows_quarantined:,}",
        json.dumps(dict(result.quarantined_by_rule)),
        " DRIFT" if result.drifted else "",
    )
    return result


def accept_drift(conn: Connection, source_file_id: str) -> int:
    """Mark a file's drift as reviewed. Reprocess the file afterwards to load those lines.

    Returns how many drift records were accepted. (Counted by selecting first: DuckDB reports
    rowcount -1 for UPDATE.)
    """
    ids = list(
        conn.scalars(
            select(schema_drift.c.id).where(
                schema_drift.c.source_file_id == source_file_id,
                schema_drift.c.resolved_at.is_(None),
            )
        )
    )
    if ids:
        conn.execute(
            update(schema_drift)
            .where(schema_drift.c.id.in_(ids))
            .values(resolved_at=datetime.now(UTC))
        )
    return len(ids)


def process_pending(
    engine: Engine, run_id: str, hospital_ids: list[str] | None = None, reprocess: bool = False
) -> list[FileResult]:
    """Process each hospital's most recent file that still needs it.

    Normally that means files with status `fetched` (new since the last run), so a re-run on
    unchanged files does nothing. reprocess=True reloads each hospital's latest file anyway,
    e.g. after a normalization rule changes.
    """
    payers = PayerMatcher()
    with engine.begin() as conn:
        sync_payers(conn, payers)
        rows = conn.execute(select(source_file).order_by(source_file.c.fetched_at)).mappings().all()
    latest: dict[str, RowMapping] = {}
    for row in rows:
        if row["status"] != FileStatus.SUPERSEDED:
            latest[row["hospital_id"]] = row

    results = []
    for hospital_id, file in latest.items():
        if hospital_ids is not None and hospital_id not in hospital_ids:
            continue
        if file["status"] != FileStatus.FETCHED and not reprocess:
            continue
        with engine.begin() as conn:
            results.append(process_file(conn, file, run_id, payers))
    payers.write_review_file()
    return results
