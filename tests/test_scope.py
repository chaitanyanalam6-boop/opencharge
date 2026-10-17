"""Out-of-area hospitals must never reach a user's search results."""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, insert

from opencharge.models import hospital, metadata
from opencharge.query.scope import searchable_hospitals
from opencharge.registry import load_registry


@pytest.fixture
def engine() -> Iterator[Engine]:
    """In-memory DuckDB with every registry hospital loaded, in-area and out-of-area alike."""
    eng = create_engine("duckdb:///:memory:")
    metadata.create_all(eng)
    rows = [
        {
            "id": h.id,
            "name": h.name,
            "address": h.address.street,
            "city": h.address.city,
            "state": h.address.state,
            "zip": h.address.zip,
            "lat": h.lat,
            "lon": h.lon,
            "ccn": h.ccn,
            "type": h.type,
            "in_service_area": h.in_service_area,
        }
        for h in load_registry().hospitals
    ]
    with eng.begin() as conn:
        conn.execute(insert(hospital), rows)
    yield eng
    eng.dispose()


def test_out_of_area_hospital_never_appears_in_search(engine: Engine) -> None:
    with engine.connect() as conn:
        visible = set(conn.scalars(searchable_hospitals()))
    assert "novant-charlotte-orthopedic" not in visible


def test_every_in_area_hospital_is_searchable(engine: Engine) -> None:
    expected = {h.id for h in load_registry().hospitals if h.in_service_area}
    with engine.connect() as conn:
        visible = set(conn.scalars(searchable_hospitals()))
    assert visible == expected


def test_out_of_area_row_is_still_stored(engine: Engine) -> None:
    # Excluded from search, not from the database: the ingest pipeline still needs it.
    with engine.connect() as conn:
        stored = set(conn.scalars(hospital.select().with_only_columns(hospital.c.id)))
    assert "novant-charlotte-orthopedic" in stored
