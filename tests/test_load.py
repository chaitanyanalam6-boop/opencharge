"""Phase 3: validation, quarantine, loading, schema drift and the database's own guarantees."""

import dataclasses
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import Engine, func, insert, select
from sqlalchemy.exc import IntegrityError

from opencharge.db import make_engine
from opencharge.ingest.fetch import run_fetch
from opencharge.ingest.polite import PoliteClient, build_http_client
from opencharge.load.pipeline import accept_drift, process_pending
from opencharge.load.runs import finish_run, start_run
from opencharge.models import (
    FileStatus,
    RunStatus,
    ingest_run,
    price_line,
    quarantined_row,
    schema_drift,
    source_file,
)
from opencharge.normalize.payers import PayerMatcher
from opencharge.normalize.record import normalize_line
from opencharge.parse.records import Code, PriceType, RawPriceLine
from opencharge.registry import Registry
from opencharge.validate.rules import RULE_NAMES, failed_rules

URL = "https://h.example/test_standardcharges.csv"
META = "hospital_name,last_updated_on,version\nTest Hospital,2026-01-01,3.0.0\n"
TALL_HEADER = (
    "description,code|1,code|1|type,setting,standard_charge|gross,standard_charge|discounted_cash,"
    "payer_name,plan_name,standard_charge|negotiated_dollar,standard_charge|methodology\n"
)

# One row per way a hospital file goes wrong, each tagged with the rule that must catch it.
CORRUPTED_ROWS = [
    ("KNEE MRI,73721,CPT,outpatient,1200,900,Aetna,PPO,850,fee schedule", None),  # valid
    ("SUPPLY ITEM,N/A,CPT,outpatient,15,,,,,", "code_present"),
    ("MYSTERY,ABCDE,CPT,outpatient,100,,,,,", "code_format_valid"),
    ("REFUND?,73722,CPT,outpatient,,,Aetna,PPO,(12.50),fee schedule", "rate_non_negative"),
    ("TYPO,73723,CPT,outpatient,9999999999,,,,,", "rate_within_sane_bounds"),
    (
        "CT HEAD,70450,CPT,outpatient,,,Mystery Health,Gold,100,fee schedule",
        "payer_resolved_if_negotiated",
    ),
    ("SHORT ROW,70460,CPT,outpatient,100", "row_misaligned"),
]


def tall_file(rows: list[str]) -> bytes:
    return (META + TALL_HEADER + "\n".join(rows) + "\n").encode()


def registry(file_format: str = "csv_tall") -> Registry:
    return Registry.model_validate(
        {
            "hospitals": [
                {
                    "id": "test-hospital",
                    "name": "Test Hospital",
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
                    "mrf_url": URL,
                    "discovery": {
                        "method": "manual",
                        "source_page_url": "https://h.example/prices",
                    },
                }
            ]
        }
    )


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    return make_engine(f"duckdb:///{tmp_path / 'test.duckdb'}")


def ingest(engine: Engine, tmp_path: Path, body: bytes, file_format: str = "csv_tall") -> str:
    """Fetch `body` as the hospital's file and process it, the way a real run does."""
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, content=body) if str(r.url) == URL else httpx.Response(404)
    )
    client = build_http_client("test@example.org", transport=transport)
    summary = run_fetch(
        engine,
        registry(file_format),
        PoliteClient(client, min_interval=0),
        raw_dir=tmp_path / "raw",
    )
    results = process_pending(engine, summary.run_id)
    finish_run(engine, summary.run_id, results)
    return summary.run_id


def count(engine: Engine, table, *where) -> int:  # type: ignore[no-untyped-def]
    with engine.connect() as conn:
        return int(conn.scalar(select(func.count()).select_from(table).where(*where)) or 0)


# --- the brief's Phase 3 acceptance test ------------------------------------------------


def test_corrupted_file_quarantines_each_bad_row_under_the_right_rule(
    engine: Engine, tmp_path: Path
) -> None:
    ingest(engine, tmp_path, tall_file([row for row, _ in CORRUPTED_ROWS]))

    with engine.connect() as conn:
        quarantined = {(q.row_number, q.failed_rule) for q in conn.execute(select(quarantined_row))}
        loaded = conn.execute(select(price_line)).mappings().all()

    expected = {(4 + i, rule) for i, (_, rule) in enumerate(CORRUPTED_ROWS) if rule}
    assert expected <= quarantined
    # Every quarantined row number belongs to a corrupted row; the valid row never appears.
    assert {n for n, _ in quarantined} == {n for n, _ in expected}

    # Zero bad rows reach price_line: only the valid row's three prices load.
    assert {(p["source_row_number"], p["price_type"]) for p in loaded} == {
        (4, "gross"),
        (4, "cash"),
        (4, "negotiated"),
    }


def test_quarantined_row_keeps_the_raw_payload(engine: Engine, tmp_path: Path) -> None:
    ingest(engine, tmp_path, tall_file([row for row, _ in CORRUPTED_ROWS]))
    with engine.connect() as conn:
        rows = {q.failed_rule: q for q in conn.execute(select(quarantined_row))}
    assert rows["rate_non_negative"].raw_payload["dollar"] == "(12.50)"
    assert rows["payer_resolved_if_negotiated"].raw_payload["payer_name"] == "Mystery Health"
    assert rows["row_misaligned"].raw_payload["cells"] == [
        "SHORT ROW",
        "70460",
        "CPT",
        "outpatient",
        "100",
    ]
    assert rows["code_present"].source_locator == "standard_charge|gross"


def test_loaded_lines_carry_full_provenance(engine: Engine, tmp_path: Path) -> None:
    ingest(engine, tmp_path, tall_file([CORRUPTED_ROWS[0][0]]))
    with engine.connect() as conn:
        line = (
            conn.execute(select(price_line).where(price_line.c.price_type == "negotiated"))
            .mappings()
            .one()
        )
        file = conn.execute(select(source_file)).mappings().one()
    assert line["source_file_id"] == file["id"]
    assert (line["source_row_number"], line["source_locator"]) == (
        4,
        "standard_charge|negotiated_dollar",
    )
    assert (line["parser_name"], line["schema_version"]) == ("cms_csv_tall_v3", "3.0.0")
    assert (line["rate_cents"], line["rate_raw"], line["payer_id"]) == (85000, "850", "aetna")
    assert file["status"] == FileStatus.LOADED
    assert file["published_date"].isoformat() == "2026-01-01"


def test_run_summary_counts_rows(engine: Engine, tmp_path: Path) -> None:
    run_id = ingest(engine, tmp_path, tall_file([row for row, _ in CORRUPTED_ROWS]))
    with engine.connect() as conn:
        run = conn.execute(select(ingest_run).where(ingest_run.c.id == run_id)).mappings().one()
    assert run["status"] == RunStatus.SUCCESS
    assert run["rows_loaded"] == 3
    assert run["rows_quarantined"] == count(engine, quarantined_row)
    assert run["rows_in"] == run["rows_loaded"] + run["rows_quarantined"]


# --- idempotence and replacement --------------------------------------------------------


def test_rerun_on_unchanged_file_is_a_no_op(engine: Engine, tmp_path: Path) -> None:
    body = tall_file([CORRUPTED_ROWS[0][0]])
    ingest(engine, tmp_path, body)
    before = (count(engine, price_line), count(engine, quarantined_row), count(engine, source_file))
    second_run = ingest(engine, tmp_path, body)
    assert (
        count(engine, price_line),
        count(engine, quarantined_row),
        count(engine, source_file),
    ) == before
    with engine.connect() as conn:
        run = conn.execute(select(ingest_run).where(ingest_run.c.id == second_run)).mappings().one()
    assert (run["rows_in"], run["rows_loaded"]) == (0, 0)


def test_reprocessing_does_not_duplicate(engine: Engine, tmp_path: Path) -> None:
    ingest(engine, tmp_path, tall_file([row for row, _ in CORRUPTED_ROWS]))
    before = (count(engine, price_line), count(engine, quarantined_row))
    process_pending(engine, start_run(engine), reprocess=True)
    assert (count(engine, price_line), count(engine, quarantined_row)) == before


def test_newer_file_supersedes_the_old_one(engine: Engine, tmp_path: Path) -> None:
    ingest(engine, tmp_path, tall_file([CORRUPTED_ROWS[0][0]]))
    ingest(
        engine,
        tmp_path,
        tall_file(["KNEE MRI,73721,CPT,outpatient,1300,950,Aetna,PPO,875,fee schedule"]),
    )
    with engine.connect() as conn:
        statuses = [
            r.status for r in conn.execute(select(source_file).order_by(source_file.c.fetched_at))
        ]
        rates = {r.price_type: r.rate_cents for r in conn.execute(select(price_line))}
    assert statuses == [FileStatus.SUPERSEDED, FileStatus.LOADED]
    assert rates == {
        "gross": 130000,
        "cash": 95000,
        "negotiated": 87500,
    }  # only the new file's prices


# --- schema drift -----------------------------------------------------------------------

WIDE_V1 = (
    META + "description,code|1,code|1|type,setting,standard_charge|gross,"
    "standard_charge|Aetna|PPO|negotiated_dollar,standard_charge|Aetna|PPO|methodology\n"
    "KNEE MRI,73721,CPT,outpatient,1200,850,fee schedule\n"
)
WIDE_V2 = (
    META + "description,code|1,code|1|type,setting,standard_charge|gross,"
    "standard_charge|Aetna|PPO|negotiated_dollar,standard_charge|Aetna|PPO|methodology,"
    "standard_charge|Cigna|HMO|negotiated_dollar\n"
    "KNEE MRI,73721,CPT,outpatient,1200,850,fee schedule,700\n"
)


def test_new_columns_are_detected_quarantined_and_surfaced(engine: Engine, tmp_path: Path) -> None:
    ingest(engine, tmp_path, WIDE_V1.encode(), "csv_wide")
    run_id = ingest(engine, tmp_path, WIDE_V2.encode(), "csv_wide")

    with engine.connect() as conn:
        drift = conn.execute(select(schema_drift)).mappings().one()
        run = conn.execute(select(ingest_run).where(ingest_run.c.id == run_id)).mappings().one()
        latest = (
            conn.execute(select(source_file).order_by(source_file.c.fetched_at.desc()))
            .mappings()
            .first()
        )
        quarantined = conn.execute(select(quarantined_row)).mappings().all()
        payers_loaded = {
            r.payer_name_raw
            for r in conn.execute(select(price_line).where(price_line.c.price_type == "negotiated"))
        }

    assert drift["added_columns"] == ["standard_charge|Cigna|HMO|negotiated_dollar"]
    assert drift["removed_columns"] == []
    assert drift["resolved_at"] is None
    assert run["status"] == RunStatus.DRIFT
    assert latest["status"] == FileStatus.DRIFT
    # The new column's price is held back; everything the previous file also had still loads.
    assert [(q["failed_rule"], q["source_locator"]) for q in quarantined] == [
        ("schema_drift", "standard_charge|Cigna|HMO|negotiated_dollar")
    ]
    assert payers_loaded == {"Aetna"}


def test_accepting_drift_loads_the_held_back_lines(engine: Engine, tmp_path: Path) -> None:
    ingest(engine, tmp_path, WIDE_V1.encode(), "csv_wide")
    ingest(engine, tmp_path, WIDE_V2.encode(), "csv_wide")
    with engine.begin() as conn:
        file_id = conn.scalar(
            select(source_file.c.id).where(source_file.c.status == FileStatus.DRIFT)
        )
        assert accept_drift(conn, file_id) == 1

    results = process_pending(engine, start_run(engine), reprocess=True)
    assert not results[0].drifted
    with engine.connect() as conn:
        payers_loaded = {
            r.payer_name_raw
            for r in conn.execute(select(price_line).where(price_line.c.price_type == "negotiated"))
        }
        status = conn.scalar(select(source_file.c.status).where(source_file.c.id == file_id))
    assert payers_loaded == {"Aetna", "Cigna"}
    assert status == FileStatus.LOADED
    assert count(engine, quarantined_row) == 0
    assert count(engine, schema_drift) == 1  # the accepted record is kept as history


def test_first_file_for_a_hospital_is_the_baseline_not_drift(
    engine: Engine, tmp_path: Path
) -> None:
    ingest(engine, tmp_path, WIDE_V2.encode(), "csv_wide")
    assert count(engine, schema_drift) == 0


# --- the database itself refuses bad rows (brief §7.3) ---------------------------------


def valid_row(engine: Engine, tmp_path: Path) -> dict[str, object]:
    ingest(engine, tmp_path, tall_file([CORRUPTED_ROWS[0][0]]))
    with engine.connect() as conn:
        row = dict(
            conn.execute(select(price_line).where(price_line.c.price_type == "gross"))
            .mappings()
            .one()
        )
    row["id"] = str(uuid.uuid4())
    return row


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"source_file_id": None}, "no provenance file"),
        ({"source_row_number": None}, "no provenance row"),
        ({"source_row_number": 0}, "row numbers start at 1"),
        ({"source_locator": ""}, "empty locator"),
        ({"parser_name": None}, "no parser"),
        ({"rate_cents": -1}, "negative money"),
        ({"price_type": "estimate"}, "unknown price type"),
        ({"code_type": "ICD10"}, "unknown code type"),
        ({"payer_id": "aetna"}, "a gross price cannot belong to a payer"),
        ({"source_file_id": "does-not-exist"}, "provenance must point at a real file"),
    ],
)
def test_database_rejects_rows_that_break_the_rules(
    engine: Engine, tmp_path: Path, change: dict[str, object], why: str
) -> None:
    row = valid_row(engine, tmp_path) | change
    with pytest.raises(Exception) as excinfo, engine.begin() as conn:
        conn.execute(insert(price_line).values(**row))
    assert (
        isinstance(excinfo.value, IntegrityError)
        or "Constraint" in type(excinfo.value).__name__
        or "constraint" in str(excinfo.value).lower()
    ), why


# --- rules in isolation -----------------------------------------------------------------


def test_price_type_known_rule() -> None:
    raw = RawPriceLine(
        4,
        "c",
        "X",
        (Code("73721", "CPT"),),
        "outpatient",
        None,
        None,
        None,
        None,
        PriceType.GROSS,
        None,
        None,
        "1",
        None,
        None,
        None,
        None,
    )
    line = normalize_line(raw, "h", PayerMatcher())
    assert failed_rules(line) == []
    bogus = dataclasses.replace(line, price_type="estimate")  # type: ignore[arg-type]
    assert failed_rules(bogus) == ["price_type_known"]


def test_rule_names_match_the_brief() -> None:
    assert set(RULE_NAMES) >= {
        "code_present",
        "code_format_valid",
        "rate_non_negative",
        "rate_within_sane_bounds",
        "price_type_known",
        "payer_resolved_if_negotiated",
    }


def test_ingested_at_is_utc(engine: Engine, tmp_path: Path) -> None:
    ingest(engine, tmp_path, tall_file([CORRUPTED_ROWS[0][0]]))
    with engine.connect() as conn:
        ingested = conn.scalar(select(price_line.c.ingested_at).limit(1))
    assert ingested.utcoffset().total_seconds() == 0
    assert abs((datetime.now(UTC) - ingested).total_seconds()) < 600
