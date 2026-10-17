"""Database tables.

Phase 1 defines the tables ingest writes to: hospital, ingest_run, source_file, fetch_attempt.
price_line, payer and quarantined_row (brief §4) arrive with the phases that write them.
Tables are created with metadata.create_all() until Alembic arrives in Phase 3
(see docs/DECISIONS.md).
"""

from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)

metadata = MetaData()


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    # At least one hospital could not be fetched; the others were still processed.
    FAILED = "failed"


class FetchOutcome(StrEnum):
    NEW = "new"  # downloaded a file we didn't have; a source_file row was written
    UNCHANGED = "unchanged"  # same sha256 as a file we already hold; nothing written
    FAILED = "failed"  # HTTP error, timeout, connection error
    BLOCKED = "blocked"  # robots.txt disallows the URL; we did not request it


class Compression(StrEnum):
    """Whether the stored bytes are themselves an archive. Parsers unwrap these later."""

    NONE = "none"
    GZIP = "gzip"
    ZIP = "zip"


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

ingest_run = Table(
    "ingest_run",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
    Column("status", String, nullable=False),
    # Row counts are filled in by the parse/load phases; fetch alone leaves them at 0.
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
    # The hospital's own last_updated_on, read from inside the file in Phase 2. Never guessed
    # from HTTP headers, so NULL until the file is parsed.
    Column("published_date", Date),
    Column("fetched_at", DateTime(timezone=True), nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("byte_size", BigInteger, nullable=False),
    Column("format", String, nullable=False),
    Column("compression", String, nullable=False),
    Column("parser_name", String, nullable=False),
    Column("local_path", Text, nullable=False),
    Column("status", String, nullable=False),
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
