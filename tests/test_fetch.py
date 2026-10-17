"""Ingest fetch behaviour against a fake hospital web server."""

import gzip
import hashlib
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from sqlalchemy import Engine, func, select

from opencharge.db import make_engine
from opencharge.ingest.fetch import run_fetch
from opencharge.ingest.polite import PoliteClient, build_http_client
from opencharge.models import FetchOutcome, RunStatus, fetch_attempt, ingest_run, source_file
from opencharge.registry import Registry

CSV_BODY = b"hospital_name,last_updated_on\nTest,2026-01-01\n"
JSON_BODY = b'{"hospital_name": "Test", "standard_charge_information": []}'


def registry(*entries: tuple[str, str, str]) -> Registry:
    """Build a registry from (id, url, format) triples."""
    return Registry.model_validate(
        {
            "hospitals": [
                {
                    "id": hospital_id,
                    "name": hospital_id.title(),
                    "ccn": "210001",
                    "address": {
                        "street": "1 Main St",
                        "city": "Baltimore",
                        "state": "MD",
                        "zip": "21201",
                    },
                    "lat": 39.29,
                    "lon": -76.61,
                    "type": "Acute Care Hospitals",
                    "format": file_format,
                    "parser": f"cms_{file_format}_v3",
                    "mrf_url": url,
                    "discovery": {
                        "method": "manual",
                        "source_page_url": "https://h.example/prices",
                    },
                }
                for hospital_id, url, file_format in entries
            ]
        }
    )


Handler = Callable[[httpx.Request], httpx.Response]


def server(routes: dict[str, httpx.Response | Handler]) -> Handler:
    """Fake web server. robots.txt is a 404 (allow all) unless a route overrides it."""

    def handler(request: httpx.Request) -> httpx.Response:
        route = routes.get(str(request.url))
        if route is None:
            return httpx.Response(404)
        return route(request) if callable(route) else route

    return handler


def polite(handler: Handler) -> PoliteClient:
    client = build_http_client("test@example.org", transport=httpx.MockTransport(handler))
    return PoliteClient(client, min_interval=0)


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    return make_engine(f"duckdb:///{tmp_path / 'test.duckdb'}")


def count(engine: Engine, table) -> int:  # type: ignore[no-untyped-def]
    with engine.connect() as conn:
        return int(conn.scalar(select(func.count()).select_from(table)) or 0)


A_URL = "https://h.example/a_standardcharges.csv"
B_URL = "https://h.example/b_standardcharges.json"


def test_new_file_is_stored_hashed_and_recorded(engine: Engine, tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    reg = registry(("a", A_URL, "csv_tall"))
    summary = run_fetch(
        engine, reg, polite(server({A_URL: httpx.Response(200, content=CSV_BODY)})), raw_dir=raw
    )

    assert summary.status is RunStatus.SUCCESS
    assert summary.results[0].outcome is FetchOutcome.NEW
    with engine.connect() as conn:
        row = conn.execute(select(source_file)).one()
    expected_sha = hashlib.sha256(CSV_BODY).hexdigest()
    assert row.sha256 == expected_sha
    assert row.byte_size == len(CSV_BODY)
    assert row.compression == "none"
    assert row.published_date is None  # read from the file in Phase 2, never guessed
    assert Path(row.local_path) == raw / "a" / f"{expected_sha}.csv"
    assert Path(row.local_path).read_bytes() == CSV_BODY


def test_rerun_on_unchanged_file_is_a_no_op(engine: Engine, tmp_path: Path) -> None:
    reg = registry(("a", A_URL, "csv_tall"))
    fake = polite(server({A_URL: httpx.Response(200, content=CSV_BODY)}))
    run_fetch(engine, reg, fake, raw_dir=tmp_path)
    second = run_fetch(engine, reg, fake, raw_dir=tmp_path)

    assert second.results[0].outcome is FetchOutcome.UNCHANGED
    assert count(engine, source_file) == 1
    assert len(list((tmp_path / "a").iterdir())) == 1  # no duplicate or leftover partial file
    # The attempt is still logged, pointing at the file it matched.
    with engine.connect() as conn:
        attempts = conn.execute(
            select(fetch_attempt.c.outcome, fetch_attempt.c.source_file_id)
        ).all()
    assert [a.outcome for a in attempts] == ["new", "unchanged"]
    assert attempts[0].source_file_id == attempts[1].source_file_id


def test_changed_file_is_recorded_as_new(engine: Engine, tmp_path: Path) -> None:
    reg = registry(("a", A_URL, "csv_tall"))
    run_fetch(
        engine,
        reg,
        polite(server({A_URL: httpx.Response(200, content=CSV_BODY)})),
        raw_dir=tmp_path,
    )
    updated = CSV_BODY + b"Test,2026-02-01\n"
    second = run_fetch(
        engine, reg, polite(server({A_URL: httpx.Response(200, content=updated)})), raw_dir=tmp_path
    )

    assert second.results[0].outcome is FetchOutcome.NEW
    assert count(engine, source_file) == 2


def test_404_fails_the_run_but_other_hospitals_still_fetch(engine: Engine, tmp_path: Path) -> None:
    reg = registry(("missing", "https://h.example/gone.csv", "csv_tall"), ("b", B_URL, "json"))
    summary = run_fetch(
        engine,
        reg,
        polite(server({B_URL: httpx.Response(200, content=JSON_BODY)})),
        raw_dir=tmp_path,
    )

    outcomes = {r.hospital_id: r for r in summary.results}
    assert outcomes["missing"].outcome is FetchOutcome.FAILED
    assert outcomes["missing"].http_status == 404
    assert outcomes["b"].outcome is FetchOutcome.NEW
    assert summary.status is RunStatus.FAILED
    with engine.connect() as conn:
        run = conn.execute(select(ingest_run)).one()
    assert run.status == "failed"
    assert run.finished_at is not None
    assert "failed=1" in run.notes and "new=1" in run.notes
    assert count(engine, source_file) == 1  # failures never create source_file rows


def test_redirects_are_followed(engine: Engine, tmp_path: Path) -> None:
    final = "https://cdn.example/a.csv"
    routes: dict[str, httpx.Response | Handler] = {
        A_URL: httpx.Response(301, headers={"Location": final}),
        final: httpx.Response(200, content=CSV_BODY),
    }
    summary = run_fetch(
        engine, registry(("a", A_URL, "csv_tall")), polite(server(routes)), raw_dir=tmp_path
    )
    assert summary.results[0].outcome is FetchOutcome.NEW


def test_gzip_transfer_encoding_is_undone_before_hashing(engine: Engine, tmp_path: Path) -> None:
    response = httpx.Response(
        200, content=gzip.compress(CSV_BODY), headers={"Content-Encoding": "gzip"}
    )
    run_fetch(
        engine,
        registry(("a", A_URL, "csv_tall")),
        polite(server({A_URL: response})),
        raw_dir=tmp_path,
    )
    with engine.connect() as conn:
        row = conn.execute(select(source_file)).one()
    assert row.sha256 == hashlib.sha256(CSV_BODY).hexdigest()
    assert row.compression == "none"


def test_gzip_file_itself_is_stored_as_is_and_flagged(engine: Engine, tmp_path: Path) -> None:
    gz = gzip.compress(CSV_BODY)
    url = "https://h.example/a.csv.gz"
    run_fetch(
        engine,
        registry(("a", url, "csv_tall")),
        polite(server({url: httpx.Response(200, content=gz)})),
        raw_dir=tmp_path,
    )
    with engine.connect() as conn:
        row = conn.execute(select(source_file)).one()
    assert row.compression == "gzip"
    assert row.local_path.endswith(".gz")
    assert Path(row.local_path).read_bytes() == gz


def test_zip_file_is_flagged(engine: Engine, tmp_path: Path) -> None:
    url = "https://h.example/a.zip"
    body = b"PK\x03\x04" + b"\x00" * 100
    run_fetch(
        engine,
        registry(("a", url, "csv_tall")),
        polite(server({url: httpx.Response(200, content=body)})),
        raw_dir=tmp_path,
    )
    with engine.connect() as conn:
        assert conn.scalar(select(source_file.c.compression)) == "zip"


def test_large_file_is_streamed_in_chunks(engine: Engine, tmp_path: Path) -> None:
    chunk = b"x" * (1024 * 1024)
    chunks_served = 0

    def body():  # type: ignore[no-untyped-def]
        nonlocal chunks_served
        for _ in range(8):
            chunks_served += 1
            yield chunk

    routes: dict[str, httpx.Response | Handler] = {
        A_URL: lambda r: httpx.Response(200, content=body())
    }
    summary = run_fetch(
        engine, registry(("a", A_URL, "csv_tall")), polite(server(routes)), raw_dir=tmp_path
    )
    assert summary.results[0].byte_size == 8 * len(chunk)
    assert chunks_served == 8


@pytest.mark.parametrize(
    ("body", "headers"),
    [
        (b"<!DOCTYPE html><html><body>Page not found</body></html>", {}),
        (b"a,b\n1,2\n", {"Content-Type": "text/html; charset=utf-8"}),
        (b"", {}),
    ],
    ids=["html-body", "html-content-type", "empty"],
)
def test_html_or_empty_200_is_a_failure_not_a_file(
    engine: Engine, tmp_path: Path, body: bytes, headers: dict[str, str]
) -> None:
    response = httpx.Response(200, content=body, headers=headers)
    summary = run_fetch(
        engine,
        registry(("a", A_URL, "csv_tall")),
        polite(server({A_URL: response})),
        raw_dir=tmp_path,
    )
    assert summary.results[0].outcome is FetchOutcome.FAILED
    assert count(engine, source_file) == 0
    assert not any((tmp_path / "a").iterdir())  # partial download cleaned up


def test_robots_disallowed_file_is_never_requested(engine: Engine, tmp_path: Path) -> None:
    requested: list[str] = []

    def file_route(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, content=CSV_BODY)

    routes: dict[str, httpx.Response | Handler] = {
        "https://h.example/robots.txt": httpx.Response(200, text="User-agent: *\nDisallow: /\n"),
        A_URL: file_route,
    }
    summary = run_fetch(
        engine, registry(("a", A_URL, "csv_tall")), polite(server(routes)), raw_dir=tmp_path
    )
    assert summary.results[0].outcome is FetchOutcome.BLOCKED
    assert requested == []
    assert summary.status is RunStatus.FAILED


def test_connection_error_is_recorded_and_run_continues(engine: Engine, tmp_path: Path) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    routes: dict[str, httpx.Response | Handler] = {
        A_URL: boom,
        B_URL: httpx.Response(200, content=JSON_BODY),
    }
    reg = registry(("a", A_URL, "csv_tall"), ("b", B_URL, "json"))
    summary = run_fetch(engine, reg, polite(server(routes)), raw_dir=tmp_path)
    assert [r.outcome for r in summary.results] == [FetchOutcome.FAILED, FetchOutcome.NEW]
    assert "ConnectError" in (summary.results[0].error or "")


def test_unknown_hospital_id_is_rejected(engine: Engine, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not in registry"):
        run_fetch(
            engine, registry(("a", A_URL, "csv_tall")), polite(server({})), ["nope"], tmp_path
        )
