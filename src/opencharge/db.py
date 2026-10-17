"""Database engine setup."""

import os

from sqlalchemy import Engine, create_engine

from opencharge import PROJECT_ROOT
from opencharge.models import metadata

DEFAULT_DATABASE_URL = f"duckdb:///{PROJECT_ROOT / 'data' / 'opencharge.duckdb'}"


def make_engine(url: str | None = None) -> Engine:
    """Create an engine for DATABASE_URL (or the local DuckDB file) and ensure tables exist."""
    url = url or os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL
    connect_args = {}
    if url.startswith("duckdb"):
        # Return timestamps in UTC everywhere, not in the local machine's timezone.
        connect_args = {"config": {"TimeZone": "UTC"}}
    engine = create_engine(url, connect_args=connect_args)
    metadata.create_all(engine)
    return engine
