"""The migrations and models.py must describe the same schema."""

from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from opencharge.db import make_engine, upgrade_to_head
from opencharge.models import CODE_TYPES, PRICE_TYPES, SETTINGS, metadata
from opencharge.normalize.codes import CodeType
from opencharge.normalize.setting import Setting
from opencharge.parse.records import PriceType


def test_migrations_produce_exactly_the_models_schema() -> None:
    # Alembic can't introspect DuckDB, so the comparison runs on SQLite; the migration
    # script itself is dialect-independent.
    engine = create_engine("sqlite://")
    upgrade_to_head(engine)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), metadata)
    assert diff == [], f"models.py and migrations disagree; generate a migration: {diff}"


def test_migrations_apply_to_duckdb_and_rerun_cleanly(tmp_path: Path) -> None:
    url = f"duckdb:///{tmp_path / 'm.duckdb'}"
    make_engine(url)
    make_engine(url)  # second upgrade is a no-op, not an error


def test_check_constraint_values_match_the_enums() -> None:
    assert set(CODE_TYPES) == {c.value for c in CodeType}
    assert set(SETTINGS) == {s.value for s in Setting}
    assert set(PRICE_TYPES) == {p.value for p in PriceType}
