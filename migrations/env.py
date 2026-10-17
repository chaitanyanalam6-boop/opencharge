"""Alembic environment: runs migrations against DATABASE_URL (or the local DuckDB file).

opencharge.db.make_engine() calls this programmatically, passing its own connection in
config.attributes["connection"]; the `alembic` CLI uses the URL instead.
"""

from alembic import context
from alembic.ddl.impl import DefaultImpl
from sqlalchemy import create_engine

from opencharge.db import database_url
from opencharge.models import metadata


class DuckDBImpl(DefaultImpl):
    """Tells Alembic the duckdb dialect behaves like a default SQL database."""

    __dialect__ = "duckdb"


def run_migrations(connection) -> None:  # type: ignore[no-untyped-def]
    context.configure(connection=connection, target_metadata=metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


connection = context.config.attributes.get("connection")
if connection is not None:
    run_migrations(connection)
else:
    engine = create_engine(database_url())
    with engine.begin() as conn:
        run_migrations(conn)
    engine.dispose()
