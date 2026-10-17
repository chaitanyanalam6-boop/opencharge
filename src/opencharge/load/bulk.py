"""Write batches of rows into a table.

DuckDB gets a fast path: the batch becomes an Arrow table and is inserted with one
INSERT ... SELECT (about 1M rows in 5 s). Any other database (Postgres in deployment) gets
SQLAlchemy's batched executemany, which is slower but has no dialect-specific code.
"""

import json
from collections.abc import Sequence
from typing import Any

import polars as pl
from sqlalchemy import JSON, BigInteger, Connection, DateTime, Integer, Table, insert


def _polars_type(column_type: Any) -> pl.DataType:
    if isinstance(column_type, BigInteger | Integer):
        return pl.Int64()
    if isinstance(column_type, DateTime):
        return pl.Datetime(time_zone="UTC")
    return pl.Utf8()


def insert_rows(conn: Connection, table: Table, rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        return
    if conn.dialect.name != "duckdb":
        conn.execute(insert(table), list(rows))
        return

    columns = [c for c in table.columns if c.name in rows[0]]
    json_columns = {c.name for c in columns if isinstance(c.type, JSON)}
    data: dict[str, list[Any]] = {c.name: [] for c in columns}
    for row in rows:
        for name in data:
            value = row.get(name)
            if name in json_columns and value is not None:
                value = json.dumps(value)
            data[name].append(value)
    frame = pl.DataFrame(data, schema={c.name: _polars_type(c.type) for c in columns})

    raw = conn.connection.driver_connection
    if raw is None:
        raise RuntimeError("DuckDB connection is closed")
    names = ", ".join(f'"{c.name}"' for c in columns)
    raw.register("_opencharge_batch", frame.to_arrow())
    try:
        raw.execute(f"INSERT INTO {table.name} ({names}) SELECT {names} FROM _opencharge_batch")
    finally:
        raw.unregister("_opencharge_batch")
