"""Build a small, real database for tests: hospitals with CSV files, run through the actual
fetch -> parse -> normalize -> validate -> load pipeline (over a fake HTTP transport)."""

from dataclasses import dataclass, field
from pathlib import Path

import httpx
from sqlalchemy import Engine

from opencharge.db import make_engine
from opencharge.ingest.fetch import run_fetch
from opencharge.ingest.polite import PoliteClient, build_http_client
from opencharge.load.pipeline import process_pending
from opencharge.load.runs import finish_run
from opencharge.registry import Registry

META = "hospital_name,last_updated_on,version\nTest Hospital,2026-01-01,3.0.0\n"
TALL_HEADER = (
    "description,code|1,code|1|type,setting,standard_charge|gross,standard_charge|discounted_cash,"
    "payer_name,plan_name,standard_charge|negotiated_dollar,standard_charge|negotiated_percentage,"
    "standard_charge|methodology\n"
)


@dataclass
class HospitalSpec:
    id: str
    lat: float
    lon: float
    rows: list[str]
    in_service_area: bool = True
    extra: dict[str, object] = field(default_factory=dict)

    @property
    def url(self) -> str:
        return f"https://{self.id}.example/standardcharges.csv"

    def body(self) -> bytes:
        return (META + TALL_HEADER + "\n".join(self.rows) + "\n").encode()


def build_database(tmp_path: Path, hospitals: list[HospitalSpec]) -> Engine:
    engine = make_engine(f"duckdb:///{tmp_path / 'test.duckdb'}")
    registry = Registry.model_validate(
        {
            "hospitals": [
                {
                    "id": h.id,
                    "name": h.id.replace("-", " ").title(),
                    "ccn": "210001",
                    "address": {
                        "street": "1 Main St",
                        "city": "Baltimore",
                        "state": "MD",
                        "zip": "21201",
                    },
                    "lat": h.lat,
                    "lon": h.lon,
                    "type": "Acute Care Hospitals",
                    "in_service_area": h.in_service_area,
                    "format": "csv_tall",
                    "parser": "cms_csv_tall_v3",
                    "mrf_url": h.url,
                    "discovery": {
                        "method": "manual",
                        "source_page_url": "https://h.example/prices",
                    },
                }
                for h in hospitals
            ]
        }
    )
    bodies = {h.url: h.body() for h in hospitals}
    transport = httpx.MockTransport(
        lambda r: (
            httpx.Response(200, content=bodies[str(r.url)])
            if str(r.url) in bodies
            else httpx.Response(404)
        )
    )
    client = build_http_client("test@example.org", transport=transport)
    summary = run_fetch(
        engine, registry, PoliteClient(client, min_interval=0), raw_dir=tmp_path / "raw"
    )
    finish_run(engine, summary.run_id, process_pending(engine, summary.run_id))
    return engine
