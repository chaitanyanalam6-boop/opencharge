"""A validated Interpretation -> prices, each with provenance, plus the gaps.

Queries are built with SQLAlchemy Core from the Interpretation's validated fields only, and
every value is a bound parameter. Nothing the LLM wrote is ever pasted into SQL text.

Gaps are first-class results ("when we don't have it, we say so"): for every in-range
hospital that has no usable price, the result says which of these it is:
  not_in_file        the hospital's file does not list this code at all
  payer_not_listed   the code is listed, but not for the requested payer
  no_rate_published  the matching lines exist but carry no dollar amount (e.g. a percentage)
"""

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import ColumnElement, Connection, and_, func, or_, select

from opencharge.models import hospital, price_line, source_file
from opencharge.query.geo import haversine_miles, locate_zip
from opencharge.query.interpret import Interpretation
from opencharge.query.scope import searchable_hospitals

# A rate published once for every insurer applies to whichever insurer was asked about.
ALL_PAYERS = "all-payers"


@dataclass(frozen=True)
class Provenance:
    source_file_id: str
    source_file_url: str
    published_date: date | None
    fetched_at: datetime
    row_number: int
    locator: str
    parser: str
    schema_version: str
    checksum: str


@dataclass(frozen=True)
class PriceResult:
    hospital_id: str
    hospital_name: str
    distance_miles: float | None
    code_type: str
    code: str
    description: str
    setting: str
    price_type: str
    payer_id: str | None
    plan_name: str | None
    rate_cents: int | None
    rate_raw: str | None
    percentage: str | None
    methodology: str | None
    issues: tuple[str, ...]
    provenance: Provenance


@dataclass(frozen=True)
class Gap:
    hospital_id: str
    hospital_name: str
    distance_miles: float | None
    kind: str
    detail: str


@dataclass(frozen=True)
class SearchResult:
    interpretation: Interpretation
    hospitals_in_range: int
    results: tuple[PriceResult, ...]
    gaps: tuple[Gap, ...]


def hospitals_in_range(
    conn: Connection, interpretation: Interpretation
) -> dict[str, tuple[str, float | None]]:
    """In-area hospitals within the radius: id -> (name, distance in miles or None)."""
    rows = conn.execute(
        select(hospital.c.id, hospital.c.name, hospital.c.lat, hospital.c.lon).where(
            hospital.c.id.in_(searchable_hospitals())
        )
    ).all()
    origin = locate_zip(interpretation.near_zip) if interpretation.near_zip else None
    found: dict[str, tuple[str, float | None]] = {}
    for hospital_id, name, lat, lon in rows:
        if origin is None:
            found[hospital_id] = (name, None)
            continue
        distance = haversine_miles(origin, (lat, lon))
        if distance <= interpretation.radius_miles:
            found[hospital_id] = (name, round(distance, 1))
    return found


def _filters(interpretation: Interpretation, hospital_ids: list[str]) -> list[ColumnElement[bool]]:
    conditions = [
        price_line.c.hospital_id.in_(hospital_ids),
        price_line.c.code_type == interpretation.code_type,
        price_line.c.code == interpretation.code,
        price_line.c.price_type.in_(interpretation.price_types),
    ]
    if interpretation.setting in ("inpatient", "outpatient"):
        # "both" and "unknown" lines may apply; they are labelled in the result.
        conditions.append(price_line.c.setting.in_([interpretation.setting, "both", "unknown"]))
    if interpretation.payer:
        conditions.append(
            or_(
                price_line.c.price_type != "negotiated",
                price_line.c.payer_id.in_([interpretation.payer, ALL_PAYERS]),
            )
        )
    return conditions


def execute(
    conn: Connection, interpretation: Interpretation, payer_name: str | None = None
) -> SearchResult:
    """Run a validated interpretation. `payer_name` is the insurer's display name, for gap text."""
    if interpretation.code is None or interpretation.code_type is None:
        raise ValueError("cannot execute an interpretation without a code; confirm it first")

    in_range = hospitals_in_range(conn, interpretation)
    hospital_ids = list(in_range)

    query = (
        select(
            price_line,
            source_file.c.url,
            source_file.c.published_date,
            source_file.c.fetched_at,
            source_file.c.sha256,
        )
        .join(source_file, source_file.c.id == price_line.c.source_file_id)
        .where(*_filters(interpretation, hospital_ids))
        .order_by(
            price_line.c.hospital_id,
            price_line.c.price_type,
            price_line.c.rate_cents.asc().nulls_last(),
        )
    )
    results = []
    for row in conn.execute(query).mappings():
        name, distance = in_range[row["hospital_id"]]
        results.append(
            PriceResult(
                hospital_id=row["hospital_id"],
                hospital_name=name,
                distance_miles=distance,
                code_type=row["code_type"],
                code=row["code"],
                description=row["description_raw"],
                setting=row["setting"],
                price_type=row["price_type"],
                payer_id=row["payer_id"],
                plan_name=row["plan_name"],
                rate_cents=row["rate_cents"],
                rate_raw=row["rate_raw"],
                percentage=row["percentage"],
                methodology=row["methodology"],
                issues=tuple(row["issues"] or ()),
                provenance=Provenance(
                    source_file_id=row["source_file_id"],
                    source_file_url=row["url"],
                    published_date=row["published_date"],
                    fetched_at=row["fetched_at"],
                    row_number=row["source_row_number"],
                    locator=row["source_locator"],
                    parser=row["parser_name"],
                    schema_version=row["schema_version"],
                    checksum=row["sha256"],
                ),
            )
        )
    return SearchResult(
        interpretation,
        len(in_range),
        tuple(results),
        tuple(_gaps(conn, interpretation, in_range, results, payer_name)),
    )


def _gaps(
    conn: Connection,
    interpretation: Interpretation,
    in_range: dict[str, tuple[str, float | None]],
    results: list[PriceResult],
    payer_name: str | None = None,
) -> list[Gap]:
    # When an insurer was named, the question is about *its* rate: a hospital that lists only a
    # cash price is still a gap for that insurer, even though it appears in the results.
    asked_for_payer = (
        interpretation.payer is not None and "negotiated" in interpretation.price_types
    )
    relevant = [r for r in results if r.price_type == "negotiated"] if asked_for_payer else results
    with_rate = {r.hospital_id for r in relevant if r.rate_cents is not None}
    with_lines = {r.hospital_id for r in relevant}
    listing = set(
        conn.scalars(
            select(price_line.c.hospital_id)
            .where(
                price_line.c.hospital_id.in_(list(in_range)),
                and_(
                    price_line.c.code_type == interpretation.code_type,
                    price_line.c.code == interpretation.code,
                ),
            )
            .group_by(price_line.c.hospital_id)
            .having(func.count() > 0)
        )
    )
    gaps = []
    for hospital_id, (name, distance) in sorted(
        in_range.items(), key=lambda kv: (kv[1][1] or 0, kv[0])
    ):
        if hospital_id in with_rate:
            continue
        if hospital_id not in listing:
            kind, detail = (
                "not_in_file",
                f"{interpretation.code_type} {interpretation.code} is not in this hospital's file.",
            )
        elif hospital_id not in with_lines:
            payer = payer_name or interpretation.payer or "the requested payer"
            kind, detail = "payer_not_listed", f"The file lists this code, but no {payer} rate."
        else:
            kind, detail = (
                "no_rate_published",
                "The file lists this code without a dollar amount (e.g. a percentage of charges).",
            )
        gaps.append(Gap(hospital_id, name, distance, kind, detail))
    return gaps
