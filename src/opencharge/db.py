"""Database engine setup. The schema is created and upgraded by Alembic migrations."""

import os

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine

from opencharge import PROJECT_ROOT

DEFAULT_DATABASE_URL = f"duckdb:///{PROJECT_ROOT / 'data' / 'opencharge.duckdb'}"
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"


def database_url() -> str:
    return os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL


def make_engine(url: str | None = None, migrate: bool = True) -> Engine:
    """Create an engine for `url` (default: DATABASE_URL or the local DuckDB file).

    With migrate=True the schema is brought up to the latest migration first, so a fresh
    checkout needs no separate setup step.
    """
    url = url or database_url()
    connect_args = {}
    if url.startswith("duckdb"):
        # Return timestamps in UTC everywhere, not in the local machine's timezone.
        connect_args = {"config": {"TimeZone": "UTC"}}
    engine = create_engine(url, connect_args=connect_args)
    if migrate:
        upgrade_to_head(engine)
    return engine


def upgrade_to_head(engine: Engine) -> None:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
