"""Assemble API responses from the database and query results. No HTTP here."""

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import Connection, Row, func, select, tuple_

from opencharge import PROJECT_ROOT
from opencharge.api.schemas import (
    DriftOut,
    FetchOut,
    GapOut,
    Headline,
    HospitalOut,
    HospitalPrices,
    HospitalStatus,
    Kpis,
    PayerRow,
    PriceOut,
    PriceSummary,
    ProvenanceOut,
    RawVsNormalized,
    RunOut,
    SourceFileOut,
    StatusResponse,
)
from opencharge.models import (
    FileStatus,
    fetch_attempt,
    hospital,
    ingest_run,
    price_line,
    quarantined_row,
    schema_drift,
    source_file,
)
from opencharge.parse.provenance import raw_csv_row
from opencharge.query.execute import Gap, PriceResult, Provenance
from opencharge.registry import load_registry
from opencharge.validate.rules import RULES, Severity

SHOPPABLE_PATH = PROJECT_ROOT / "sources" / "shoppable_services.yaml"
# Reading a row back out of a very large CSV takes seconds; above this, we skip it.
MAX_SOURCE_ROW_FILE_BYTES = 150_000_000


def provenance_out(p: Provenance) -> ProvenanceOut:
    return ProvenanceOut(
        source_file_id=p.source_file_id,
        source_file_url=p.source_file_url,
        published_date=p.published_date,
        fetched_at=p.fetched_at,
        row_number=p.row_number,
        locator=p.locator,
        parser=p.parser,
        schema_version=p.schema_version,
        checksum=p.checksum,
    )


def price_out(r: PriceResult, payer_names: dict[str, str]) -> PriceOut:
    return PriceOut(
        price_type=r.price_type,
        rate_cents=r.rate_cents,
        rate_raw=r.rate_raw,
        percentage=r.percentage,
        methodology=r.methodology,
        payer_id=r.payer_id,
        payer_name=payer_names.get(r.payer_id) if r.payer_id else None,
        plan_name=r.plan_name,
        setting=r.setting,
        description=r.description,
        issues=list(r.issues),
        provenance=provenance_out(r.provenance),
    )


def summarize(lines: list[PriceResult], payer_names: dict[str, str]) -> PriceSummary:
    priced = [r for r in lines if r.rate_cents is not None]
    lowest = min(priced, key=lambda r: r.rate_cents or 0) if priced else None
    highest = max(priced, key=lambda r: r.rate_cents or 0) if priced else None
    return PriceSummary(
        count=len(lines),
        with_rate=len(priced),
        lowest=(
            price_out(lowest, payer_names)
            if lowest
            else (price_out(lines[0], payer_names) if lines else None)
        ),
        highest=price_out(highest, payer_names) if highest else None,
    )


def hospitals_out(conn: Connection) -> dict[str, HospitalOut]:
    rows = conn.execute(select(hospital.c.id, hospital.c.name, hospital.c.city, hospital.c.state))
    return {
        r.id: HospitalOut(id=r.id, name=r.name, city=r.city, state=r.state, distance_miles=None)
        for r in rows
    }


def hospital_prices(
    results: tuple[PriceResult, ...],
    hospitals: dict[str, HospitalOut],
    payer_names: dict[str, str],
    price_types: Sequence[str],
) -> list[HospitalPrices]:
    """One row per hospital (docs/DECISIONS.md): each price type summarized as lowest/highest,
    every individual line one click away on the detail page."""
    by_hospital: dict[str, list[PriceResult]] = defaultdict(list)
    for r in results:
        by_hospital[r.hospital_id].append(r)

    rows = []
    for hospital_id, lines in by_hospital.items():
        first = lines[0]
        by_type: dict[str, list[PriceResult]] = defaultdict(list)
        for line in lines:
            by_type[line.price_type].append(line)
        rows.append(
            HospitalPrices(
                hospital=hospitals[hospital_id].model_copy(
                    update={"distance_miles": first.distance_miles}
                ),
                prices={t: summarize(by_type[t], payer_names) for t in price_types if t in by_type},
                description_variants=len({line.description for line in lines}),
                detail_url=f"/api/hospital/{hospital_id}/price/{first.code}?code_type={first.code_type}",
            )
        )
    primary = primary_price_type(rows, price_types)
    rows.sort(key=lambda h: _sort_key(h, primary))
    return rows


def primary_price_type(rows: list[HospitalPrices], price_types: Sequence[str]) -> str | None:
    """The first requested price type that at least one hospital publishes with a rate."""
    for t in price_types:
        if any(h.prices.get(t) and h.prices[t].with_rate for h in rows):
            return t
    return None


def _sort_key(h: HospitalPrices, primary: str | None) -> tuple[int, int, float]:
    summary = h.prices.get(primary) if primary else None
    cents = summary.lowest.rate_cents if summary and summary.lowest else None
    return (cents is None, cents or 0, h.hospital.distance_miles or 0.0)


def headline(rows: list[HospitalPrices], price_types: Sequence[str]) -> Headline | None:
    """Cheapest vs most expensive hospital for the primary price type."""
    primary = primary_price_type(rows, price_types)
    if primary is None:
        return None
    priced: list[tuple[str, int]] = []
    for h in rows:
        summary = h.prices.get(primary)
        if summary and summary.lowest and summary.lowest.rate_cents is not None:
            priced.append((h.hospital.name, summary.lowest.rate_cents))
    low = min(priced, key=lambda p: p[1])
    high = max(priced, key=lambda p: p[1])
    return Headline(
        price_type=primary,
        lowest_hospital=low[0],
        lowest_cents=low[1],
        highest_hospital=high[0],
        highest_cents=high[1],
        spread_cents=high[1] - low[1],
        hospitals_with_price=len(priced),
    )


def gaps_out(gaps: tuple[Gap, ...], hospitals: dict[str, HospitalOut]) -> list[GapOut]:
    return [
        GapOut(
            hospital=hospitals[g.hospital_id].model_copy(
                update={"distance_miles": g.distance_miles}
            ),
            kind=g.kind,
            detail=g.detail,
        )
        for g in gaps
    ]


# --- source files -----------------------------------------------------------------------


def source_file_out(row: Any) -> SourceFileOut:
    registry = {h.id: h for h in load_registry().hospitals}
    entry = registry.get(row.hospital_id)
    age = (datetime.now(UTC).date() - row.published_date).days if row.published_date else None
    return SourceFileOut(
        id=row.id,
        hospital_id=row.hospital_id,
        url=row.url,
        source_page_url=str(entry.discovery.source_page_url) if entry else None,
        file_name=row.url.rstrip("/").rsplit("/", 1)[-1],
        published_date=row.published_date,
        fetched_at=row.fetched_at,
        age_days=age,
        sha256=row.sha256,
        byte_size=row.byte_size,
        format=row.format,
        parser_name=row.parser_name,
        schema_version=row.schema_version,
        status=row.status,
        rows_in=row.rows_in,
        rows_loaded=row.rows_loaded,
        rows_quarantined=row.rows_quarantined,
    )


# --- hospital price detail --------------------------------------------------------------


def payer_table(
    conn: Connection, hospital_id: str, negotiated: list[PriceResult], payer_names: dict[str, str]
) -> list[PayerRow]:
    """One row per payer/plan with a rate for this code, then every payer/plan the hospital
    lists elsewhere in its file but not for this code ("not published", never $0)."""
    rows: dict[tuple[str | None, str | None], PriceResult] = {}
    for r in sorted(negotiated, key=lambda r: (r.rate_cents is None, r.rate_cents or 0)):
        rows.setdefault((r.payer_id, r.plan_name), r)
    table = [
        PayerRow(
            payer_id=r.payer_id,
            payer_name=payer_names.get(r.payer_id or ""),
            plan_name=r.plan_name,
            rate_cents=r.rate_cents,
            percentage=r.percentage,
            methodology=r.methodology,
            published=True,
            provenance=provenance_out(r.provenance),
        )
        for r in rows.values()
    ]
    elsewhere = conn.execute(
        select(price_line.c.payer_id, price_line.c.plan_name)
        .where(price_line.c.hospital_id == hospital_id, price_line.c.price_type == "negotiated")
        .group_by(price_line.c.payer_id, price_line.c.plan_name)
    ).all()
    for payer_id, plan_name in sorted(elsewhere, key=lambda p: (p[0] or "", p[1] or "")):
        if (payer_id, plan_name) not in rows:
            table.append(
                PayerRow(
                    payer_id=payer_id,
                    payer_name=payer_names.get(payer_id or ""),
                    plan_name=plan_name,
                    rate_cents=None,
                    percentage=None,
                    methodology=None,
                    published=False,
                    provenance=None,
                )
            )
    return table


def raw_vs_normalized(conn: Connection, line: PriceResult) -> RawVsNormalized:
    stored = (
        conn.execute(
            select(price_line).where(
                price_line.c.source_file_id == line.provenance.source_file_id,
                price_line.c.source_row_number == line.provenance.row_number,
                price_line.c.source_locator == line.provenance.locator,
            )
        )
        .mappings()
        .first()
    )
    file = conn.execute(
        select(source_file).where(source_file.c.id == line.provenance.source_file_id)
    ).one()
    source_row = None
    path = Path(file.local_path)
    if file.format != "json" and path.exists() and file.byte_size <= MAX_SOURCE_ROW_FILE_BYTES:
        row = raw_csv_row(path, line.provenance.row_number)
        source_row = {k: v for k, v in row.items() if v.strip()}
    assert stored is not None
    return RawVsNormalized(
        source_row=source_row,
        raw={
            "row_number": stored["source_row_number"],
            "column": stored["source_locator"],
            "description": stored["description_raw"],
            "payer_name": stored["payer_name_raw"],
            "plan_name": stored["plan_name"],
            "value": stored["rate_raw"] or stored["percentage"],
        },
        normalized={
            "code_type": stored["code_type"],
            "code": stored["code"],
            "setting": stored["setting"],
            "price_type": stored["price_type"],
            "payer_id": stored["payer_id"],
            "rate_cents": stored["rate_cents"],
            "issues": list(stored["issues"] or []),
        },
    )


def caveats(hospital_state: str, file: SourceFileOut) -> list[str]:
    notes = [
        "This is the hospital's facility charge. Doctors, anesthesia and radiologists may bill "
        "separately.",
        "What you pay also depends on your deductible, copay and coinsurance.",
    ]
    if file.age_days is not None:
        notes.append(
            f"The hospital published this file {file.age_days} days ago; prices may have changed."
        )
    if hospital_state == "MD":
        notes.append(
            "Maryland sets hospital rates for all insurers (HSCRC), so negotiated rates often "
            "equal the gross charge; differences between hospitals matter more than between "
            "insurers."
        )
    return notes


VALIDATION_CHECKS = [rule.name for rule in RULES if rule.severity is Severity.REJECT]


# --- status -----------------------------------------------------------------------------


def shoppable_services() -> list[dict[str, Any]]:
    with SHOPPABLE_PATH.open(encoding="utf-8") as f:
        return list(yaml.safe_load(f)["services"])


def shoppable_coverage(conn: Connection) -> dict[str, int]:
    """hospital_id -> how many of CMS's 70 shoppable services it publishes a dollar rate for."""
    services = shoppable_services()
    wanted = {(s["code_type"], code) for s in services for code in s["codes"]}
    priced = conn.execute(
        select(price_line.c.hospital_id, price_line.c.code_type, price_line.c.code)
        .where(
            tuple_(price_line.c.code_type, price_line.c.code).in_(list(wanted)),
            price_line.c.rate_cents.is_not(None),
        )
        .group_by(price_line.c.hospital_id, price_line.c.code_type, price_line.c.code)
    ).all()
    have: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for hospital_id, code_type, code in priced:
        have[hospital_id].add((code_type, code))
    return {
        hospital_id: sum(
            1 for s in services if any((s["code_type"], c) in codes for c in s["codes"])
        )
        for hospital_id, codes in have.items()
    }


def _pill(file: Row[Any] | None, last_fetch: Row[Any] | None) -> str:
    if last_fetch is not None and last_fetch.outcome in ("failed", "blocked"):
        return "Fetch failed"
    if file is None:
        return "Not fetched"
    if file.status == FileStatus.DRIFT:
        return "Drift"
    return "Loaded" if file.status == FileStatus.LOADED else file.status.title()


def drift_summary(added: list[str], removed: list[str]) -> str:
    parts = []
    if added:
        parts.append(f"{len(added)} new column{'s' if len(added) != 1 else ''} (e.g. {added[0]})")
    if removed:
        parts.append(
            f"{len(removed)} column{'s' if len(removed) != 1 else ''} gone (e.g. {removed[0]})"
        )
    return (
        "The hospital's file layout changed since its previous file: "
        + " and ".join(parts)
        + ". Prices from new columns are held in quarantine until someone reviews the change."
    )


def status(conn: Connection, answer_quality: dict[str, float] | None) -> StatusResponse:
    registry = {h.id: h for h in load_registry().hospitals}
    coverage = shoppable_coverage(conn)
    services_total = len(shoppable_services())

    files = conn.execute(select(source_file).order_by(source_file.c.fetched_at)).all()
    current = {f.hospital_id: f for f in files if f.status != FileStatus.SUPERSEDED}
    fetches = conn.execute(select(fetch_attempt).order_by(fetch_attempt.c.attempted_at)).all()
    last_fetch = {f.hospital_id: f for f in fetches}

    hospitals = []
    for h in conn.execute(
        select(hospital).order_by(hospital.c.in_service_area.desc(), hospital.c.name)
    ):
        file, fetch = current.get(h.id), last_fetch.get(h.id)
        hospitals.append(
            HospitalStatus(
                id=h.id,
                name=h.name,
                format=registry[h.id].format.value if h.id in registry else "unknown",
                in_service_area=h.in_service_area,
                status=_pill(file, fetch),
                last_fetch=(
                    FetchOut(
                        outcome=fetch.outcome,
                        attempted_at=fetch.attempted_at,
                        http_status=fetch.http_status,
                        error=fetch.error,
                    )
                    if fetch
                    else None
                ),
                current_file=source_file_out(file) if file else None,
                shoppable_services_covered=coverage.get(h.id, 0),
            )
        )

    in_area = [h for h in hospitals if h.in_service_area]
    loaded = sum((h.current_file.rows_loaded or 0) for h in in_area if h.current_file)
    quarantined = sum((h.current_file.rows_quarantined or 0) for h in in_area if h.current_file)
    in_area_ids = [h.id for h in in_area]
    covered_any = _services_covered_by_any(conn, in_area_ids)
    current_ids = [f.id for f in current.values() if f.hospital_id in in_area_ids]
    by_rule: dict[str, int] = dict(
        conn.execute(
            select(quarantined_row.c.failed_rule, func.count())
            .where(quarantined_row.c.source_file_id.in_(current_ids))
            .group_by(quarantined_row.c.failed_rule)
        ).all()
    )
    drifts = conn.execute(
        select(schema_drift, source_file.c.hospital_id)
        .join(source_file, source_file.c.id == schema_drift.c.source_file_id)
        .where(schema_drift.c.resolved_at.is_(None))
        .order_by(schema_drift.c.detected_at.desc())
    ).all()
    runs = conn.execute(select(ingest_run).order_by(ingest_run.c.started_at.desc()).limit(10)).all()
    finished = [r.finished_at for r in runs if r.finished_at is not None]

    return StatusResponse(
        kpis=Kpis(
            hospitals_tracked=len(in_area),
            price_lines_loaded=loaded,
            rows_quarantined=quarantined,
            quarantined_pct=(
                round(100 * quarantined / (loaded + quarantined), 2)
                if loaded + quarantined
                else 0.0
            ),
            shoppable_services_total=services_total,
            shoppable_services_covered=covered_any,
            last_refresh=max(finished) if finished else None,
        ),
        hospitals=hospitals,
        quarantined_by_rule={k: int(v) for k, v in sorted(by_rule.items(), key=lambda kv: -kv[1])},
        needs_attention=[
            DriftOut(
                hospital_id=d.hospital_id,
                source_file_id=d.source_file_id,
                detected_at=d.detected_at,
                added_columns=d.added_columns,
                removed_columns=d.removed_columns,
                summary=drift_summary(d.added_columns, d.removed_columns),
            )
            for d in drifts
        ],
        recent_runs=[
            RunOut(
                id=r.id,
                started_at=r.started_at,
                finished_at=r.finished_at,
                status=r.status,
                rows_in=r.rows_in,
                rows_loaded=r.rows_loaded,
                rows_quarantined=r.rows_quarantined,
            )
            for r in runs
        ],
        answer_quality=answer_quality,
    )


def _services_covered_by_any(conn: Connection, hospital_ids: list[str]) -> int:
    services = shoppable_services()
    wanted = {(s["code_type"], code) for s in services for code in s["codes"]}
    priced = set(
        conn.execute(
            select(price_line.c.code_type, price_line.c.code)
            .where(
                tuple_(price_line.c.code_type, price_line.c.code).in_(list(wanted)),
                price_line.c.rate_cents.is_not(None),
                price_line.c.hospital_id.in_(hospital_ids),
            )
            .group_by(price_line.c.code_type, price_line.c.code)
        ).all()
    )
    return sum(1 for s in services if any((s["code_type"], c) in priced for c in s["codes"]))
