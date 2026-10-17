"""Phase 4: retrieval, LLM interpretation guardrails, and parameterized execution.

The LLM is a scripted fake here, so these tests are deterministic, free, and run in CI.
"""

import json
from pathlib import Path

import pytest
from sqlalchemy import Engine, select

from opencharge.models import payer
from opencharge.query.catalog import CodeCatalog, tokenize
from opencharge.query.execute import execute
from opencharge.query.interpret import Interpretation, InterpretationError, interpret
from tests.helpers import HospitalSpec, build_database

# Baltimore-area coordinates (ZIP 21201 is at 39.295, -76.622).
DOWNTOWN = (39.2924, -76.6135)  # ~0.5 mi from 21201
TOWSON = (39.3899, -76.6280)  # ~6.6 mi
FREDERICK = (39.4143, -77.4105)  # ~43 mi
CHARLOTTE = (35.2108, -80.8245)  # out of area

KNEE_MRI = "MRI LWR EXT JOINT WO CON"


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    return build_database(
        tmp_path,
        [
            HospitalSpec(
                "downtown-hospital",
                *DOWNTOWN,
                [
                    f"{KNEE_MRI},73721,CPT,outpatient,1200,900,Aetna,PPO,850,,fee schedule",
                    f"{KNEE_MRI},73721,CPT,outpatient,1200,900,Cigna,HMO,700,,fee schedule",
                    "CT HEAD OR BRAIN WO CONT,70450,CPT,outpatient,800,600,"
                    "Aetna,PPO,500,,fee schedule",
                    "MRI UPR EXT JOINT WO CON,73221,CPT,outpatient,1100,850,,,,,",
                ],
            ),
            HospitalSpec(
                "towson-hospital",
                *TOWSON,
                [
                    "BC-MRI LREX JT WO C LTD KNE LT,73721,CPT,outpatient,1500,1100,"
                    "ALL PAYERS,ALL PLANS,1500,,other",
                ],
            ),
            # Lists the code, but Aetna's rate is a percentage only: a gap, not a price.
            HospitalSpec(
                "pct-hospital",
                39.30,
                -76.60,
                [
                    f"{KNEE_MRI},73721,CPT,outpatient,,,Aetna,PPO,,92.3,"
                    "percent of total billed charges"
                ],
            ),
            # In range but does not list the code at all.
            HospitalSpec(
                "no-mri-hospital",
                39.31,
                -76.62,
                ["CT HEAD OR BRAIN WO CONT,70450,CPT,outpatient,900,700,,,,,"],
            ),
            HospitalSpec(
                "frederick-hospital",
                *FREDERICK,
                [f"{KNEE_MRI},73721,CPT,outpatient,1000,800,Aetna,PPO,600,,fee schedule"],
            ),
            # Cheapest of all, but out of the service area: must never appear.
            HospitalSpec(
                "charlotte-hospital",
                *CHARLOTTE,
                [f"{KNEE_MRI},73721,CPT,outpatient,100,50,Aetna,PPO,40,,fee schedule"],
                in_service_area=False,
            ),
        ],
    )


@pytest.fixture
def catalog(engine: Engine) -> CodeCatalog:
    with engine.connect() as conn:
        return CodeCatalog.build(conn)


@pytest.fixture
def payers(engine: Engine) -> dict[str, str]:
    with engine.connect() as conn:
        return dict(conn.execute(select(payer.c.id, payer.c.canonical_name)).all())


class FakeLLM:
    """Returns scripted replies in order and records every prompt it was sent."""

    def __init__(self, *replies: object) -> None:
        self.replies = [r if isinstance(r, str) else json.dumps(r) for r in replies]
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.replies.pop(0)


EXTRACT_OK = {
    "description_terms": ["mri", "knee", "lower extremity", "joint"],
    "payer": "aetna",
    "setting": "outpatient",
    "price_types": ["negotiated", "cash"],
    "near_zip": "21201",
    "radius_miles": 25,
    "unresolved": [],
}
CHOOSE_OK = {"code_type": "CPT", "code": "73721", "confidence": 0.92, "unresolved": []}


# --- retrieval --------------------------------------------------------------------------


def test_tokenize_expands_hospital_abbreviations() -> None:
    assert tokenize("MRI LWR EXT JOINT WO CON") == [
        "mri",
        "lower",
        "extremity",
        "joint",
        "without",
        "contrast",
    ]
    assert {"knee", "lower", "extremity", "joint"} <= set(
        tokenize("BC-MRI LREX JT WO C LTD KNE LT")
    )
    assert tokenize("HC CT Head W/O contrast") == ["ct", "head", "without", "contrast"]


def test_search_ranks_the_right_code_first(catalog: CodeCatalog) -> None:
    results = catalog.search(["mri", "knee", "lower extremity", "joint"])
    assert results[0].code == "73721"
    assert results[0].hospital_count == 4  # downtown, towson, pct, frederick; not charlotte
    assert "70450" not in [c.code for c in results[:1]]


def test_search_finds_a_code_typed_directly(catalog: CodeCatalog) -> None:
    assert catalog.search(["73221"])[0].code == "73221"


def test_catalog_excludes_out_of_area_hospitals(tmp_path: Path) -> None:
    engine = build_database(
        tmp_path,
        [
            HospitalSpec(
                "charlotte",
                *CHARLOTTE,
                ["ONLY HERE,99999,CPT,outpatient,10,,,,,,"],
                in_service_area=False,
            )
        ],
    )
    with engine.connect() as conn:
        assert CodeCatalog.build(conn).search(["only", "here"]) == []


# --- interpretation guardrails ----------------------------------------------------------


def test_interpret_happy_path(catalog: CodeCatalog, payers: dict[str, str]) -> None:
    llm = FakeLLM(EXTRACT_OK, CHOOSE_OK)
    result = interpret("knee MRI near Baltimore with Aetna", llm, catalog, payers)
    assert (result.code_type, result.code, result.payer, result.near_zip) == (
        "CPT",
        "73721",
        "aetna",
        "21201",
    )
    assert not result.needs_confirmation
    assert len(llm.calls) == 2


def test_the_llm_never_sees_a_price(catalog: CodeCatalog, payers: dict[str, str]) -> None:
    llm = FakeLLM(EXTRACT_OK, CHOOSE_OK)
    interpret("knee MRI near Baltimore with Aetna", llm, catalog, payers)
    prompts = " ".join(system + user for system, user in llm.calls)
    for price in ("850", "700", "1200", "1500", "$"):
        assert price not in prompts, f"{price!r} leaked into a prompt"


def test_invalid_json_is_retried_once_with_the_error(
    catalog: CodeCatalog, payers: dict[str, str]
) -> None:
    llm = FakeLLM("Sure! Here is the JSON: {", EXTRACT_OK, CHOOSE_OK)
    result = interpret("knee MRI with Aetna", llm, catalog, payers)
    assert result.code == "73721"
    assert "previous reply was rejected" in llm.calls[1][1]


@pytest.mark.parametrize(
    ("bad_reply", "why"),
    [
        (EXTRACT_OK | {"price": 850}, "an extra field could smuggle a number"),
        (EXTRACT_OK | {"payer": "bright-health"}, "payer must be a canonical id we hold"),
        (EXTRACT_OK | {"unresolved": ["costs about $850"]}, "no dollar amounts in free text"),
        (
            EXTRACT_OK | {"description_terms": ["mri'; DROP TABLE price_line; --"]},
            "terms are plain words",
        ),
        (EXTRACT_OK | {"near_zip": "Baltimore"}, "ZIP is five digits"),
        (EXTRACT_OK | {"price_types": ["estimate"]}, "price types are a fixed set"),
    ],
)
def test_bad_extraction_is_rejected_twice_then_fails_cleanly(
    catalog: CodeCatalog, payers: dict[str, str], bad_reply: dict[str, object], why: str
) -> None:
    llm = FakeLLM(bad_reply, bad_reply)
    with pytest.raises(InterpretationError, match="couldn't turn that question into a search"):
        interpret("knee MRI", llm, catalog, payers)
    assert len(llm.calls) == 2, why


def test_a_code_not_on_the_shortlist_is_rejected(
    catalog: CodeCatalog, payers: dict[str, str]
) -> None:
    invented = {"code_type": "CPT", "code": "73722", "confidence": 0.99, "unresolved": []}
    llm = FakeLLM(EXTRACT_OK, invented, invented)
    with pytest.raises(InterpretationError):
        interpret("knee MRI", llm, catalog, payers)


def test_a_price_in_the_code_choice_is_rejected(
    catalog: CodeCatalog, payers: dict[str, str]
) -> None:
    with_price = CHOOSE_OK | {"estimated_price": 850}
    llm = FakeLLM(EXTRACT_OK, with_price, CHOOSE_OK)
    assert interpret("knee MRI", llm, catalog, payers).code == "73721"  # corrected on retry


@pytest.mark.parametrize(
    ("extract", "choose", "reason"),
    [
        (EXTRACT_OK, CHOOSE_OK | {"confidence": 0.4}, "low confidence"),
        (EXTRACT_OK | {"unresolved": ["payer: Bright Health"]}, CHOOSE_OK, "unknown insurer"),
        (
            EXTRACT_OK,
            {"code_type": None, "code": None, "confidence": 0.0, "unresolved": []},
            "no code fits",
        ),
        (EXTRACT_OK | {"near_zip": "90210"}, CHOOSE_OK, "ZIP outside our area"),
    ],
)
def test_uncertain_interpretations_ask_for_confirmation(
    catalog: CodeCatalog,
    payers: dict[str, str],
    extract: dict[str, object],
    choose: dict[str, object],
    reason: str,
) -> None:
    result = interpret("knee MRI", FakeLLM(extract, choose), catalog, payers)
    assert result.needs_confirmation, reason


def test_overlong_question_is_refused_without_calling_the_llm(
    catalog: CodeCatalog, payers: dict[str, str]
) -> None:
    llm = FakeLLM()
    with pytest.raises(InterpretationError):
        interpret("x" * 301, llm, catalog, payers)
    assert llm.calls == []


# --- execution --------------------------------------------------------------------------


def interpretation(**overrides: object) -> Interpretation:
    fields: dict[str, object] = dict(
        code_type="CPT",
        code="73721",
        description_terms=["mri", "knee"],
        payer="aetna",
        setting="outpatient",
        price_types=["negotiated", "cash"],
        near_zip="21201",
        radius_miles=25,
        confidence=0.9,
        unresolved=[],
    )
    fields.update(overrides)
    return Interpretation(**fields)  # type: ignore[arg-type]


def test_execute_returns_prices_with_provenance(engine: Engine) -> None:
    with engine.connect() as conn:
        result = execute(conn, interpretation())
    downtown = [r for r in result.results if r.hospital_id == "downtown-hospital"]
    assert {(r.price_type, r.payer_id, r.rate_cents) for r in downtown} == {
        ("negotiated", "aetna", 85000),
        ("cash", None, 90000),
    }
    for r in result.results:
        assert r.provenance.row_number >= 4
        assert r.provenance.locator
        assert len(r.provenance.checksum) == 64
        assert r.provenance.source_file_url.startswith("https://")


def test_execute_never_returns_out_of_area_hospitals(engine: Engine) -> None:
    with engine.connect() as conn:
        result = execute(conn, interpretation(near_zip=None))  # no radius: whole service area
    assert "charlotte-hospital" not in {r.hospital_id for r in result.results}
    assert "charlotte-hospital" not in {g.hospital_id for g in result.gaps}


def test_radius_limits_hospitals(engine: Engine) -> None:
    with engine.connect() as conn:
        near = execute(conn, interpretation(radius_miles=25))
        far = execute(conn, interpretation(radius_miles=60))
    assert "frederick-hospital" not in {r.hospital_id for r in near.results}
    assert "frederick-hospital" in {r.hospital_id for r in far.results}
    assert all(r.distance_miles is not None and r.distance_miles <= 25 for r in near.results)


def test_all_payer_rate_counts_for_the_requested_insurer(engine: Engine) -> None:
    with engine.connect() as conn:
        result = execute(conn, interpretation())
    towson = [
        r
        for r in result.results
        if r.hospital_id == "towson-hospital" and r.price_type == "negotiated"
    ]
    assert [(r.payer_id, r.rate_cents) for r in towson] == [("all-payers", 150000)]


def test_other_insurers_rates_are_excluded(engine: Engine) -> None:
    with engine.connect() as conn:
        result = execute(conn, interpretation())
    assert "cigna" not in {r.payer_id for r in result.results}


def test_gaps_say_why_a_hospital_has_no_price(engine: Engine) -> None:
    with engine.connect() as conn:
        result = execute(conn, interpretation())
    gaps = {g.hospital_id: g.kind for g in result.gaps}
    assert gaps == {"pct-hospital": "no_rate_published", "no-mri-hospital": "not_in_file"}
    pct = [r for r in result.results if r.hospital_id == "pct-hospital"]
    assert [(r.rate_cents, r.percentage) for r in pct] == [(None, "92.3")]  # shown, never as $0


def test_payer_not_listed_gap(engine: Engine) -> None:
    with engine.connect() as conn:
        result = execute(conn, interpretation(payer="humana", price_types=["negotiated"]))
    gaps = {g.hospital_id: g.kind for g in result.gaps}
    assert gaps["downtown-hospital"] == "payer_not_listed"
    assert "towson-hospital" not in gaps  # its all-payer rate covers Humana too


def test_execute_uses_bound_parameters_only(engine: Engine) -> None:
    hostile = "73721' OR '1'='1"
    with pytest.raises(ValueError):
        interpretation(code=hostile)  # the model refuses it outright...
    # ...and even if validation were bypassed, the executor binds it as a plain value.
    forged = Interpretation.model_construct(**(interpretation().model_dump() | {"code": hostile}))
    with engine.connect() as conn:
        result = execute(conn, forged)
    assert result.results == ()
    assert all(g.kind == "not_in_file" for g in result.gaps)


def test_execute_refuses_an_unconfirmed_interpretation(engine: Engine) -> None:
    with engine.connect() as conn, pytest.raises(ValueError, match="confirm"):
        execute(conn, interpretation(code=None, code_type=None))
