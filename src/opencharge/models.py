"""Database tables.

The schema is managed by Alembic (migrations/). tests/test_migrations.py checks that the
migrations produce exactly the tables defined here.

Enumerated columns are plain strings guarded by CHECK constraints, which behave the same in
DuckDB and Postgres. The allowed values are listed here as literals (importing the enums
would create an import cycle); tests/test_models.py checks they match the Python enums.
"""

from enum import StrEnum

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)

metadata = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    # At least one hospital could not be fetched; the others were still processed.
    FAILED = "failed"
    # A file's columns changed since the hospital's previous file; see schema_drift.
    DRIFT = "drift"


class FetchOutcome(StrEnum):
    NEW = "new"  # downloaded a file we didn't have; a source_file row was written
    UNCHANGED = "unchanged"  # same sha256 as a file we already hold; nothing written
    FAILED = "failed"  # HTTP error, timeout, connection error
    BLOCKED = "blocked"  # robots.txt disallows the URL; we did not request it


class FileStatus(StrEnum):
    FETCHED = "fetched"  # downloaded, not yet processed
    LOADED = "loaded"  # its valid price lines are live
    DRIFT = "drift"  # loaded, but columns changed: lines from new columns are quarantined
    SUPERSEDED = "superseded"  # a newer file from the same hospital replaced its lines
    FAILED = "failed"  # could not be parsed at all


class Compression(StrEnum):
    """Whether the stored bytes are themselves an archive. Parsers unwrap these later."""

    NONE = "none"
    GZIP = "gzip"
    ZIP = "zip"


CODE_TYPES = ("CPT", "HCPCS", "DRG", "NDC", "LOCAL", "REV")
SETTINGS = ("inpatient", "outpatient", "both", "unknown")
PRICE_TYPES = ("gross", "cash", "negotiated", "min", "max")


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


hospital = Table(
    "hospital",
    metadata,
    # Registry slug (e.g. "mercy-medical-center"): stable across re-ingests and readable in logs.
    Column("id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("address", String, nullable=False),
    Column("city", String, nullable=False),
    Column("state", String(2), nullable=False),
    Column("zip", String(5), nullable=False),
    Column("lat", Float, nullable=False),
    Column("lon", Float, nullable=False),
    Column("ccn", String(6), nullable=False),
    Column("type", String, nullable=False),
    # Out-of-area hospitals are ingested but excluded from search; see query/scope.py.
    Column(
        "in_service_area",
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    ),
)

payer = Table(
    "payer",
    metadata,
    Column("id", String, primary_key=True),  # e.g. "aetna"; synced from payer_aliases.yaml
    Column("canonical_name", String, nullable=False),
    Column("aliases", JSON, nullable=False),  # the match patterns, for display and audit
)

ingest_run = Table(
    "ingest_run",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
    Column("status", String, nullable=False),
    Column("rows_in", BigInteger, nullable=False, default=0),
    Column("rows_loaded", BigInteger, nullable=False, default=0),
    Column("rows_quarantined", BigInteger, nullable=False, default=0),
    Column("notes", Text),
)

source_file = Table(
    "source_file",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("hospital_id", String, ForeignKey("hospital.id"), nullable=False),
    Column("ingest_run_id", String(36), ForeignKey("ingest_run.id"), nullable=False),
    Column("url", Text, nullable=False),
    # The hospital's own last_updated_on, read from inside the file. Never guessed from HTTP
    # headers, so NULL until the file is parsed (or if the hospital wrote an unknown format).
    Column("published_date", Date),
    Column("fetched_at", DateTime(timezone=True), nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("byte_size", BigInteger, nullable=False),
    Column("format", String, nullable=False),
    Column("compression", String, nullable=False),
    Column("parser_name", String, nullable=False),
    Column("local_path", Text, nullable=False),
    Column("status", String, nullable=False),
    # Filled in when the file is processed.
    Column("schema_version", String),  # the CMS template version the file declares
    Column("columns", JSON),  # CSV header / JSON key paths, for drift detection
    Column("column_fingerprint", String(64)),
    Column("rows_in", BigInteger),
    Column("rows_loaded", BigInteger),
    Column("rows_quarantined", BigInteger),
    # The same bytes from the same hospital are one file, however many times we fetch them.
    UniqueConstraint("hospital_id", "sha256", name="uq_source_file_hospital_sha"),
)

fetch_attempt = Table(
    "fetch_attempt",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("ingest_run_id", String(36), ForeignKey("ingest_run.id"), nullable=False),
    Column("hospital_id", String, ForeignKey("hospital.id"), nullable=False),
    Column("url", Text, nullable=False),
    Column("attempted_at", DateTime(timezone=True), nullable=False),
    Column("outcome", String, nullable=False),
    Column("http_status", Integer),
    Column("error", Text),
    # Set when outcome is "new" (the file just stored) or "unchanged" (the file it matched).
    Column("source_file_id", String(36), ForeignKey("source_file.id")),
)

price_line = Table(
    "price_line",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("hospital_id", String, ForeignKey("hospital.id"), nullable=False),
    Column("code_type", String, nullable=False),
    Column("code", String, nullable=False),
    Column("other_codes", JSON),  # [[type, code], ...] from the same item
    Column("description_raw", Text, nullable=False),
    Column("description_clean", Text, nullable=False),
    Column("setting", String, nullable=False),
    Column("price_type", String, nullable=False),
    Column("payer_id", String, ForeignKey("payer.id")),
    Column("payer_name_raw", Text),
    Column("plan_name", Text),
    # NULL means "published but empty" (or no dollar figure at all). Never 0 for "missing".
    Column("rate_cents", BigInteger),
    Column("rate_raw", Text),  # exactly what the hospital wrote
    Column("percentage", Text),
    Column("algorithm", Text),
    Column("methodology", Text),
    Column("billing_class", Text),
    Column("modifiers", Text),
    Column("drug_unit", Text),
    Column("drug_type", Text),
    Column("notes", Text),
    Column("issues", JSON),  # non-fatal normalization notes, e.g. "sub_cent_rounded"
    # Provenance: required on every row, enforced by the database (brief §7.3).
    Column("source_file_id", String(36), ForeignKey("source_file.id"), nullable=False),
    Column("source_row_number", BigInteger, nullable=False),
    Column("source_locator", Text, nullable=False),
    Column("parser_name", String, nullable=False),
    Column("schema_version", String, nullable=False),
    Column("ingested_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(_one_of("code_type", CODE_TYPES), name="code_type_known"),
    CheckConstraint(_one_of("setting", SETTINGS), name="setting_known"),
    CheckConstraint(_one_of("price_type", PRICE_TYPES), name="price_type_known"),
    CheckConstraint("rate_cents IS NULL OR rate_cents >= 0", name="rate_non_negative"),
    CheckConstraint("source_row_number > 0", name="source_row_positive"),
    CheckConstraint("length(source_locator) > 0", name="source_locator_present"),
    # Only negotiated rates belong to a payer.
    CheckConstraint(
        "price_type = 'negotiated' OR payer_id IS NULL", name="payer_only_if_negotiated"
    ),
    Index("ix_price_line_code", "code_type", "code"),
    Index("ix_price_line_hospital_id", "hospital_id"),
    Index("ix_price_line_source_file_id", "source_file_id"),
)

quarantined_row = Table(
    "quarantined_row",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("source_file_id", String(36), ForeignKey("source_file.id"), nullable=False),
    Column("ingest_run_id", String(36), ForeignKey("ingest_run.id"), nullable=False),
    Column("row_number", BigInteger, nullable=False),
    Column("source_locator", Text),  # NULL when a whole row was rejected
    Column("raw_payload", JSON, nullable=False),
    Column("failed_rule", String, nullable=False),
    Column("detail", Text),  # every rule the line failed, when more than one
    Column("failed_at", DateTime(timezone=True), nullable=False),
    Index("ix_quarantined_row_source_file_id", "source_file_id"),
)

schema_drift = Table(
    "schema_drift",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("source_file_id", String(36), ForeignKey("source_file.id"), nullable=False),
    Column("previous_source_file_id", String(36), ForeignKey("source_file.id"), nullable=False),
    Column("ingest_run_id", String(36), ForeignKey("ingest_run.id"), nullable=False),
    Column("added_columns", JSON, nullable=False),
    Column("removed_columns", JSON, nullable=False),
    Column("detected_at", DateTime(timezone=True), nullable=False),
    Column("resolved_at", DateTime(timezone=True)),  # set when a person accepts the change
)
