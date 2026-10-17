"""API response models. These are the contract the frontend builds against.

Every price object carries a `provenance` block (brief §5, Phase 5). Money is always integer
cents; `rate_cents: null` means "not published", never zero.
"""

from datetime import date, datetime

from pydantic import BaseModel, Field

from opencharge.query.interpret import Interpretation

NOTICE = "Built from public CMS price transparency files; not medical or billing advice."


class ProvenanceOut(BaseModel):
    source_file_id: str
    source_file_url: str
    published_date: date | None = Field(description="The hospital's own last_updated_on.")
    fetched_at: datetime
    row_number: int = Field(description="CSV: spreadsheet row. JSON: 1-based item index.")
    locator: str = Field(description="CSV column name, or JSON pointer to the exact value.")
    parser: str
    schema_version: str
    checksum: str = Field(description="sha256 of the file as downloaded.")


class PriceOut(BaseModel):
    price_type: str
    rate_cents: int | None = Field(description="Integer cents. null = not published (never 0).")
    rate_raw: str | None = Field(description="Exactly what the hospital wrote.")
    percentage: str | None = Field(description="Published percentage of charges, if any.")
    methodology: str | None
    payer_id: str | None
    payer_name: str | None = Field(description="Canonical payer name.")
    plan_name: str | None
    setting: str
    description: str = Field(description="The hospital's own description of the item.")
    issues: list[str] = Field(description="Normalization notes, e.g. rate_within_sane_bounds.")
    provenance: ProvenanceOut


class PriceSummary(BaseModel):
    """All lines of one price type at one hospital, summarized (one row per hospital)."""

    count: int = Field(description="Published lines of this type for the code.")
    with_rate: int = Field(description="How many of those carry a dollar amount.")
    lowest: PriceOut | None
    highest: PriceOut | None


class HospitalOut(BaseModel):
    id: str
    name: str
    city: str
    state: str
    distance_miles: float | None


class HospitalPrices(BaseModel):
    hospital: HospitalOut
    prices: dict[str, PriceSummary] = Field(description="Keyed by price type.")
    description_variants: int = Field(description="How many ways this hospital describes the code.")
    detail_url: str


class GapOut(BaseModel):
    hospital: HospitalOut
    kind: str = Field(description="not_in_file | payer_not_listed | no_rate_published")
    detail: str


class Headline(BaseModel):
    price_type: str
    lowest_hospital: str
    lowest_cents: int
    highest_hospital: str
    highest_cents: int
    spread_cents: int
    hospitals_with_price: int


class SearchResponse(BaseModel):
    status: str = Field(description="ok | needs_confirmation")
    question: str | None
    interpretation: Interpretation
    message: str | None = None
    headline: Headline | None = None
    hospitals: list[HospitalPrices] = []
    gaps: list[GapOut] = []
    hospitals_in_range: int | None = None
    notice: str = NOTICE


class ProcedureResponse(BaseModel):
    code_type: str
    code: str
    descriptions: list[str] = Field(description="Most common hospital descriptions of the code.")
    hospitals: list[HospitalPrices]
    gaps: list[GapOut]
    notice: str = NOTICE


class PayerRow(BaseModel):
    payer_id: str | None
    payer_name: str | None
    plan_name: str | None
    rate_cents: int | None
    percentage: str | None
    methodology: str | None
    published: bool = Field(
        description="False: the hospital lists this payer, but not for this code."
    )
    provenance: ProvenanceOut | None


class RawVsNormalized(BaseModel):
    """One line as the hospital wrote it, next to what we stored."""

    source_row: dict[str, str] | None = Field(
        description="The original CSV row or JSON item, when the source file is available locally."
    )
    raw: dict[str, str | int | None]
    normalized: dict[str, str | int | list[str] | None]


class SourceFileOut(BaseModel):
    id: str
    hospital_id: str
    url: str
    source_page_url: str | None
    file_name: str
    published_date: date | None
    fetched_at: datetime
    age_days: int | None
    sha256: str
    byte_size: int
    format: str
    parser_name: str
    schema_version: str | None
    status: str
    rows_in: int | None
    rows_loaded: int | None
    rows_quarantined: int | None


class HospitalPriceDetail(BaseModel):
    hospital: HospitalOut
    code_type: str
    code: str
    descriptions: list[str]
    gross: PriceSummary
    cash: PriceSummary
    payers: list[PayerRow]
    source_file: SourceFileOut
    validation_checks: list[str] = Field(description="Rules every loaded line passed.")
    caveats: list[str]
    example_line: RawVsNormalized
    notice: str = NOTICE


class FetchOut(BaseModel):
    outcome: str
    attempted_at: datetime
    http_status: int | None
    error: str | None


class HospitalStatus(BaseModel):
    id: str
    name: str
    format: str
    in_service_area: bool
    status: str = Field(description="Loaded | Drift | Fetch failed | Not fetched")
    last_fetch: FetchOut | None
    current_file: SourceFileOut | None
    shoppable_services_covered: int


class DriftOut(BaseModel):
    hospital_id: str
    source_file_id: str
    detected_at: datetime
    added_columns: list[str]
    removed_columns: list[str]
    summary: str


class RunOut(BaseModel):
    id: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    rows_in: int
    rows_loaded: int
    rows_quarantined: int


class Kpis(BaseModel):
    hospitals_tracked: int
    price_lines_loaded: int
    rows_quarantined: int
    quarantined_pct: float = Field(description="Quarantined as a % of all rows taken in.")
    shoppable_services_total: int
    shoppable_services_covered: int = Field(
        description="Of CMS's 70, how many at least one hospital prices."
    )
    last_refresh: datetime | None


class StatusResponse(BaseModel):
    kpis: Kpis
    hospitals: list[HospitalStatus]
    quarantined_by_rule: dict[str, int]
    needs_attention: list[DriftOut]
    recent_runs: list[RunOut]
    answer_quality: dict[str, float] | None = Field(description="Latest eval results (Phase 7).")
    notice: str = NOTICE


class ErrorOut(BaseModel):
    error: str
