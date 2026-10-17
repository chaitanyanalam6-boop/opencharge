"""Database tables.

Phase 1 defines only `hospital`. The remaining tables in brief §4 (price_line, payer,
source_file, quarantined_row, ingest_run) arrive with the phases that write to them.
"""

from sqlalchemy import Boolean, Column, Float, MetaData, String, Table, text

metadata = MetaData()

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
