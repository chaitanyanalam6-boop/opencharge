"""Phase 5: the HTTP API, against a real (temporary) database and a scripted LLM."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from opencharge.api.app import create_app
from opencharge.api.schemas import NOTICE
from tests.helpers import FakeLLM, HospitalSpec, build_database

KNEE = "MRI LWR EXT JOINT WO CON"
DOWNTOWN = (39.2924, -76.6135)
TOWSON = (39.3899, -76.6280)
CHARLOTTE = (35.2108, -80.8245)

EXTRACT = {
    "description_terms": ["mri", "knee", "lower extremity", "joint"],
    "payer": "aetna",
    "setting": "outpatient",
    "price_types": ["negotiated", "cash"],
    "near_zip": "21201",
    "radius_miles": 25,
    "unresolved": [],
}
CHOOSE = {"code_type": "CPT", "code": "73721", "confidence": 0.9, "unresolved": []}
PROVENANCE_KEYS = {
    "source_file_url",
    "published_date",
    "fetched_at",
    "row_number",
    "parser",
    "checksum",
}


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    return build_database(
        tmp_path,
        [
            HospitalSpec(
                "downtown-hospital",
                *DOWNTOWN,
                [
                    f"{KNEE},73721,CPT,outpatient,1200,900,Aetna,PPO,850,,fee schedule",
                    f"{KNEE} BILAT,73721,CPT,outpatient,2000,1500,Aetna,PPO,1400,,fee schedule",
                    "CT HEAD WO CONT,70450,CPT,outpatient,800,600,Cigna,HMO,500,,fee schedule",
                ],
            ),
            HospitalSpec(
                "towson-hospital",
                *TOWSON,
                [f"{KNEE},73721,CPT,outpatient,1500,1100,Aetna,PPO,1300,,other"],
            ),
            HospitalSpec(
                "cash-only-hospital", 39.30, -76.60, [f"{KNEE},73721,CPT,outpatient,1000,700,,,,,"]
            ),
            HospitalSpec(
                "charlotte-hospital",
                *CHARLOTTE,
                [f"{KNEE},73721,CPT,outpatient,100,50,Aetna,PPO,40,,fee schedule"],
                in_service_area=False,
            ),
        ],
    )


def client_for(engine: Engine, llm: FakeLLM | None) -> Iterator[TestClient]:
    with TestClient(create_app(engine, llm, use_env_llm=False)) as client:
        yield client


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM(EXTRACT, CHOOSE)


@pytest.fixture
def client(engine: Engine, llm: FakeLLM) -> Iterator[TestClient]:
    yield from client_for(engine, llm)


def all_prices(body: dict) -> list[dict]:  # type: ignore[type-arg]
    return [
        price
        for h in body["hospitals"]
        for summary in h["prices"].values()
        for price in (summary["lowest"], summary["highest"])
        if price
    ]


# --- /api/search ------------------------------------------------------------------------


def test_search_returns_interpretation_prices_and_gaps(client: TestClient) -> None:
    body = client.get("/api/search", params={"q": "knee MRI near Baltimore with Aetna"}).json()
    assert body["status"] == "ok"
    assert body["interpretation"]["code"] == "73721"
    # Sorted by the primary price type (Aetna's negotiated rate), cheapest first; a hospital
    # without that rate sorts last rather than being dropped.
    assert [h["hospital"]["id"] for h in body["hospitals"]] == [
        "downtown-hospital",
        "towson-hospital",
        "cash-only-hospital",
    ]
    assert {g["hospital"]["id"]: g["kind"] for g in body["gaps"]} == {
        "cash-only-hospital": "payer_not_listed"
    }
    assert body["notice"] == NOTICE


def test_one_row_per_hospital_with_lowest_and_highest(client: TestClient) -> None:
    body = client.get("/api/search", params={"q": "knee MRI with Aetna"}).json()
    downtown = next(h for h in body["hospitals"] if h["hospital"]["id"] == "downtown-hospital")
    negotiated = downtown["prices"]["negotiated"]
    assert (
        negotiated["count"],
        negotiated["lowest"]["rate_cents"],
        negotiated["highest"]["rate_cents"],
    ) == (
        2,
        85000,
        140000,
    )
    assert negotiated["typical"]["rate_cents"] == 85000  # lower middle of [850, 1400]
    assert downtown["description_variants"] == 2
    assert downtown["detail_url"] == "/api/hospital/downtown-hospital/price/73721?code_type=CPT"


def test_headline_states_the_spread(client: TestClient) -> None:
    body = client.get("/api/search", params={"q": "knee MRI with Aetna"}).json()
    # Downtown publishes $850 and $1,400 (a bilateral variant); its typical line is the lower
    # middle, $850, a real published line. Towson's only line is $1,300.
    assert body["headline"] == {
        "price_type": "negotiated",
        "basis": "typical",
        "lowest_hospital": "Downtown Hospital",
        "lowest_cents": 85000,
        "highest_hospital": "Towson Hospital",
        "highest_cents": 130000,
        "spread_cents": 45000,
        "hospitals_with_price": 2,
    }


def test_every_price_carries_its_provenance(client: TestClient) -> None:
    body = client.get("/api/search", params={"q": "knee MRI with Aetna"}).json()
    prices = all_prices(body)
    assert prices
    for price in prices:
        assert price["provenance"].keys() >= PROVENANCE_KEYS
        assert price["provenance"]["row_number"] >= 4


def test_out_of_area_hospital_never_appears(client: TestClient) -> None:
    body = client.get("/api/search", params={"q": "knee MRI with Aetna"}).json()
    ids = {h["hospital"]["id"] for h in body["hospitals"]} | {
        g["hospital"]["id"] for g in body["gaps"]
    }
    assert "charlotte-hospital" not in ids
    assert all(p["rate_cents"] != 4000 for p in all_prices(body))


def test_uncertain_interpretation_asks_instead_of_searching(engine: Engine) -> None:
    llm = FakeLLM(EXTRACT, CHOOSE | {"confidence": 0.3})
    for client in client_for(engine, llm):
        body = client.get("/api/search", params={"q": "that scan for my knee thing"}).json()
    assert body["status"] == "needs_confirmation"
    assert body["hospitals"] == [] and body["headline"] is None
    assert body["interpretation"]["code"] == "73721"


def test_uninterpretable_question_is_a_clean_422(engine: Engine) -> None:
    llm = FakeLLM("not json", "still not json")
    for client in client_for(engine, llm):
        response = client.get("/api/search", params={"q": "asdf"})
    assert response.status_code == 422
    assert "couldn't turn that question into a search" in response.json()["error"]


def test_search_without_an_llm_is_503(engine: Engine) -> None:
    for client in client_for(engine, None):
        response = client.get("/api/search", params={"q": "knee MRI"})
    assert response.status_code == 503


def test_repeated_question_is_served_from_cache(client: TestClient, llm: FakeLLM) -> None:
    first = client.get("/api/search", params={"q": "knee MRI with Aetna"}).json()
    second = client.get("/api/search", params={"q": "  Knee MRI   with aetna "}).json()
    assert first == second
    assert len(llm.calls) == 2  # one extract + one choose, for both requests together


def test_search_is_rate_limited(engine: Engine) -> None:
    llm = FakeLLM(*([EXTRACT, CHOOSE] * 11))
    for client in client_for(engine, llm):
        statuses = [
            client.get("/api/search", params={"q": f"knee MRI {i}"}).status_code for i in range(11)
        ]
        last = client.get("/api/search", params={"q": "knee MRI again"})
    assert statuses[:10] == [200] * 10
    assert statuses[10] == 429
    assert int(last.headers["Retry-After"]) >= 1


# --- /api/search/run (the "Correct this" button) ---------------------------------------


def corrected(**overrides: object) -> dict[str, object]:
    return {
        "code_type": "CPT",
        "code": "73721",
        "description_terms": ["mri"],
        "payer": "aetna",
        "setting": None,
        "price_types": ["negotiated", "cash"],
        "near_zip": "21201",
        "radius_miles": 25,
        "confidence": 0.2,
        "unresolved": ["was unsure"],
    } | overrides


def test_corrected_interpretation_runs_without_the_llm(client: TestClient, llm: FakeLLM) -> None:
    body = client.post("/api/search/run", json=corrected()).json()
    assert body["status"] == "ok"
    assert body["hospitals"]
    assert llm.calls == []


@pytest.mark.parametrize(
    ("change", "status"),
    [
        ({"code": "99999"}, 422),  # not published by any hospital we cover
        ({"payer": "bright-health"}, 422),  # not a payer we know
        ({"code": "73721; DROP TABLE price_line"}, 422),  # fails the model's pattern
        ({"price_types": ["estimate"]}, 422),
        ({"extra_field": 1}, 422),
    ],
)
def test_corrected_interpretation_is_validated(
    client: TestClient, change: dict[str, object], status: int
) -> None:
    assert client.post("/api/search/run", json=corrected(**change)).status_code == status


# --- /api/procedure, /api/hospital/.../price, /api/sources -------------------------------


def test_procedure_lists_every_in_area_hospital(client: TestClient) -> None:
    body = client.get("/api/procedure/73721").json()
    assert body["code_type"] == "CPT"
    assert {h["hospital"]["id"] for h in body["hospitals"]} == {
        "downtown-hospital",
        "towson-hospital",
        "cash-only-hospital",
    }
    assert KNEE in body["descriptions"]


def test_unknown_procedure_is_404(client: TestClient) -> None:
    response = client.get("/api/procedure/12345")
    assert response.status_code == 404
    assert "No hospital we cover" in response.json()["error"]


def test_hospital_price_detail(client: TestClient) -> None:
    body = client.get("/api/hospital/downtown-hospital/price/73721?code_type=CPT").json()
    assert body["gross"]["lowest"]["rate_cents"] == 120000
    assert body["cash"]["lowest"]["rate_cents"] == 90000
    payers = {(p["payer_id"], p["plan_name"]): p for p in body["payers"]}
    assert payers[("aetna", "PPO")]["rate_cents"] == 85000 and payers[("aetna", "PPO")]["published"]
    # Cigna is in this hospital's file (for the CT), but not for this code: "not published".
    assert payers[("cigna", "HMO")] == {
        "payer_id": "cigna",
        "payer_name": "Cigna",
        "plan_name": "HMO",
        "rate_cents": None,
        "percentage": None,
        "methodology": None,
        "published": False,
        "provenance": None,
    }
    assert "payer_resolved_if_negotiated" in body["validation_checks"]
    assert any("bill separately" in c for c in body["caveats"])
    assert any("Maryland sets hospital rates" in c for c in body["caveats"])


def test_detail_shows_the_raw_row_next_to_the_normalized_record(client: TestClient) -> None:
    line = client.get("/api/hospital/downtown-hospital/price/73721?code_type=CPT").json()[
        "example_line"
    ]
    assert line["source_row"]["description"] == KNEE
    assert line["source_row"]["standard_charge|negotiated_dollar"] == "850"
    assert line["raw"]["value"] == "850"
    assert line["normalized"]["rate_cents"] == 85000
    assert line["normalized"]["payer_id"] == "aetna"


def test_out_of_area_hospital_detail_is_404(client: TestClient) -> None:
    assert client.get("/api/hospital/charlotte-hospital/price/73721").status_code == 404


def test_code_not_at_that_hospital_is_404(client: TestClient) -> None:
    response = client.get("/api/hospital/towson-hospital/price/70450?code_type=CPT")
    assert response.status_code == 404
    assert "not in this hospital's file" in response.json()["error"]


def test_source_file_endpoint(client: TestClient) -> None:
    detail = client.get("/api/hospital/downtown-hospital/price/73721?code_type=CPT").json()
    source = client.get(f"/api/sources/{detail['source_file']['id']}").json()
    assert source["url"] == "https://downtown-hospital.example/standardcharges.csv"
    assert len(source["sha256"]) == 64
    assert source["status"] == "loaded"
    assert client.get("/api/sources/does-not-exist").status_code == 404


# --- /api/status ------------------------------------------------------------------------


def test_status_reports_kpis_and_every_hospital(client: TestClient) -> None:
    body = client.get("/api/status").json()
    assert body["kpis"]["hospitals_tracked"] == 3  # in-area only
    assert body["kpis"]["shoppable_services_total"] == 70
    assert body["kpis"]["shoppable_services_covered"] == 2  # 73721 and 70450 are on CMS's list
    statuses = {h["id"]: (h["status"], h["in_service_area"]) for h in body["hospitals"]}
    assert statuses["charlotte-hospital"] == ("Loaded", False)  # shown, labelled out of area
    assert statuses["downtown-hospital"] == ("Loaded", True)
    assert body["recent_runs"][0]["status"] == "success"
    assert body["needs_attention"] == []


# --- the OpenAPI docs -------------------------------------------------------------------


def test_openapi_documents_every_endpoint_with_real_examples(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    for path in [
        "/api/search",
        "/api/procedure/{code}",
        "/api/hospital/{hospital_id}/price/{code}",
        "/api/status",
        "/api/sources/{file_id}",
    ]:
        example = spec["paths"][path]["get"]["responses"]["200"]["content"]["application/json"].get(
            "example"
        )
        assert example, f"{path} has no example"
    search_example = spec["paths"]["/api/search"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["example"]
    assert search_example["interpretation"]["code"] == "73721"
    assert "medstarhealth.org" in str(search_example)  # captured from a real hospital file


def test_typical_price_is_a_real_published_line_never_an_average(engine: Engine) -> None:
    from opencharge.api.views import summarize
    from opencharge.query.execute import execute
    from opencharge.query.interpret import Interpretation

    with engine.connect() as conn:
        result = execute(
            conn,
            Interpretation(
                code_type="CPT",
                code="73721",
                description_terms=["mri"],
                payer="aetna",
                setting=None,
                price_types=["negotiated"],
                near_zip=None,
                radius_miles=25,
                confidence=1.0,
                unresolved=[],
            ),
        )
    lines = [r for r in result.results if r.hospital_id == "downtown-hospital"]
    typical = summarize(lines, {}).typical
    assert typical is not None
    assert typical.rate_cents in {r.rate_cents for r in lines}  # 112,500 (the mean) would fail
    assert typical.provenance.row_number in {r.provenance.row_number for r in lines}


def test_gap_names_the_insurer(client: TestClient) -> None:
    body = client.get("/api/search", params={"q": "knee MRI with Aetna"}).json()
    assert body["gaps"][0]["detail"] == "The file lists this code, but no Aetna rate."


def test_payers_lists_canonical_payers(client: TestClient) -> None:
    payers = client.get("/api/payers").json()
    assert {"id": "aetna", "name": "Aetna"} in payers
    assert [p["name"] for p in payers] == sorted(p["name"] for p in payers)
