"""Normalization, tested against the messy values real hospital files contain.

Each case names the file it was seen in where it came from real data.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from opencharge.normalize.codes import CodeType, classify, primary_code
from opencharge.normalize.money import parse_money, parse_percentage
from opencharge.normalize.payers import PayerMatcher, clean_payer_text
from opencharge.normalize.record import normalize_line
from opencharge.normalize.setting import Setting, normalize_setting
from opencharge.normalize.text import clean_description, parse_published_date
from opencharge.parse.records import Code, PriceType, RawPriceLine

# --- money ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "cents", "issue"),
    [
        ("319", 31900, None),  # Mercy: whole dollars
        ("95.21", 9521, None),
        ("$1,200.00", 120000, None),  # dollar sign and thousands separator
        (" 1,200 ", 120000, None),  # stray whitespace
        ("3793.7662", 379377, "sub_cent_rounded"),  # MedStar cash price, 4 decimals
        ("3793.765", 379377, "sub_cent_rounded"),  # half-up, not banker's rounding
        ("0.10", 10, None),  # small but real
        ("0", 0, None),  # a genuine $0 stays 0 ...
        ("N/A", None, "published_as_empty"),  # ... but "no price" is never 0
        ("n/a", None, "published_as_empty"),
        ("-", None, "published_as_empty"),
        ("NULL", None, "published_as_empty"),
        ("", None, "published_as_empty"),
        ("(12.50)", -1250, None),  # accounting negative; validation rejects it later
        ("1.2E+3", 120000, None),  # scientific notation from a spreadsheet export
        ("1000.0", 100000, None),  # JSON number written with a trailing .0
        ("USD 45", 4500, None),
        ("Varies", None, "unparseable_amount"),
        ("see contract", None, "unparseable_amount"),
        ("Infinity", None, "unparseable_amount"),
    ],
)
def test_parse_money(raw: str, cents: int | None, issue: str | None) -> None:
    result = parse_money(raw)
    assert (result.cents, result.issue) == (cents, issue)


def test_absent_money_is_none_without_an_issue() -> None:
    # Percentage-only negotiated rates (Novant) have no dollar at all: not an error.
    assert parse_money(None).cents is None
    assert parse_money(None).issue is None


def test_money_is_always_an_int_never_a_float() -> None:
    assert type(parse_money("19.99").cents) is int


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("92.3", Decimal("92.3")),
        ("80%", Decimal("80")),
        (" 55.0 ", Decimal("55.0")),
        ("abc", None),
        (None, None),
    ],
)
def test_parse_percentage(raw: str | None, expected: Decimal | None) -> None:
    assert parse_percentage(raw) == expected


# --- codes ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "declared", "expected_type", "expected_code", "issue"),
    [
        ("73721", "CPT", CodeType.CPT, "73721", None),
        ("90480", "HCPCS", CodeType.CPT, "90480", None),  # Mercy labels CPT codes as HCPCS
        ("J1100", "HCPCS", CodeType.HCPCS, "J1100", None),
        ("j1100", "hcpcs", CodeType.HCPCS, "J1100", None),  # lowercase
        ("0042T", "CPT", CodeType.CPT, "0042T", None),  # Category III
        ("0001A", "CPT", CodeType.CPT, "0001A", None),  # vaccine administration
        ("123U", "CPT", CodeType.CPT, "0123U", "leading_zeros_restored"),  # Novant
        ("100", "CPT", CodeType.CPT, "00100", "leading_zeros_restored"),  # anesthesia, Novant
        ("ABCDE", "CPT", CodeType.CPT, "ABCDE", "code_shape_mismatch"),
        ("470", "MS-DRG", CodeType.DRG, "470", None),
        ("47", "MS-DRG", CodeType.DRG, "047", None),
        ("361", "RC", CodeType.REV, "0361", None),  # MedStar drops the leading zero
        ("0003-2560-16", "NDC", CodeType.NDC, "0003-2560-16", None),
        ("121053005", "NDC", CodeType.NDC, "121053005", "code_shape_mismatch"),  # Mercy, not padded
        ("2210000001", "CDM", CodeType.LOCAL, "2210000001", None),
        ("27447", "", CodeType.CPT, "27447", "undeclared_code_type"),  # brief: 5 digits -> CPT
        ("G0378", "", CodeType.HCPCS, "G0378", "undeclared_code_type"),  # brief: G/J -> HCPCS
        ("470", "", CodeType.DRG, "470", "undeclared_code_type"),  # brief: 3 digits -> DRG
        ("X99", "APC", CodeType.LOCAL, "X99", "unsearched_code_type:APC"),
    ],
)
def test_classify(
    code: str, declared: str, expected_type: CodeType, expected_code: str, issue: str | None
) -> None:
    assert classify(code, declared) == (expected_type, expected_code, issue)


def test_primary_code_prefers_what_people_search_by() -> None:
    # MedStar rows carry CDM, RC and CPT; the CPT is what "knee MRI" resolves to.
    codes = (Code("32710154", "CDM"), Code("361", "RC"), Code("10030", "CPT"))
    primary, others, issues = primary_code(codes)
    assert primary == (CodeType.CPT, "10030")
    assert others == ((CodeType.LOCAL, "32710154"), (CodeType.REV, "0361"))
    assert issues == ()


def test_placeholder_code_is_ignored_and_reported() -> None:
    primary, _, issues = primary_code((Code("2500000001", "CDM"), Code("0", "RC")))
    assert primary == (CodeType.LOCAL, "2500000001")
    assert any(i.startswith("placeholder_code_ignored") for i in issues)


def test_no_codes_is_reported() -> None:
    assert primary_code(()) == (None, (), ("no_code",))


# --- setting ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "column", "expected"),
    [
        ("BOTH", None, Setting.BOTH),  # Mercy
        ("Outpatient", None, Setting.OUTPATIENT),  # MedStar
        (" inpatient ", None, Setting.INPATIENT),
        ("IP", None, Setting.INPATIENT),
        ("Inpatient/Outpatient", None, Setting.BOTH),
        (None, "standard_charge|Aetna|PPO Inpatient|negotiated_dollar", Setting.INPATIENT),
        (None, "standard_charge|Aetna|PPO|negotiated_dollar", Setting.UNKNOWN),
        (None, None, Setting.UNKNOWN),
    ],
)
def test_normalize_setting(value: str | None, column: str | None, expected: Setting) -> None:
    assert normalize_setting(value, column)[0] is expected


def test_unrecognized_setting_is_unknown_and_reported() -> None:
    assert normalize_setting("ER", None) == (Setting.UNKNOWN, "unrecognized_setting:ER")


# --- payers -----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def payers() -> PayerMatcher:
    return PayerMatcher()


@pytest.mark.parametrize(
    ("payer", "plan", "expected"),
    [
        ("Aetna US HealthCare Inc", "Aetna All HMO and PPO Plans", "aetna"),  # MedStar
        ("Aetna Medicare", "Aetna Medicare", "aetna"),  # insurer, not "medicare"
        ("AETNA [204]", "PB Aetna Commercial", "aetna"),  # GBMC bracketed internal code
        ("CAREFIRST BLUE CHOICE HMO [253]", None, "carefirst"),
        ("Bcbs", "Carefirst Medicare Managed Care Plan", "carefirst"),  # JHHC: plan names it
        ("Bcbs", "All Commercial Plans", None),  # ambiguous: left for review
        ("UHC COMMUNITY PLAN MCO [240]", None, "unitedhealthcare"),
        ("United Healthcare-Ovations", "United Secure", "unitedhealthcare"),
        ("MAMSI", "MAMSI MDIPA/Optimum Choice", "unitedhealthcare"),
        ("AmeriGroup", "AmeriGroup DC", "wellpoint"),
        ("Private HealthCare Systems (PHCS)", None, "multiplan"),
        ("MEDICARE [245]", None, "medicare"),
        ("MEDICARE RAILROAD [246]", None, "medicare"),
        ("AmeriHealth", "Medicaid", None),  # Novant: which AmeriHealth? Not guessed.
        ("PFC", "PFC", None),
        (None, None, None),
    ],
)
def test_payer_matching(
    payers: PayerMatcher, payer: str | None, plan: str | None, expected: str | None
) -> None:
    assert payers.match(payer, plan) == expected


def test_unmatched_payers_are_logged_for_review(tmp_path: Path) -> None:
    matcher = PayerMatcher()
    matcher.resolve("h1", "PFC", "PFC")
    matcher.resolve("h1", "PFC", "PFC")
    matcher.resolve("h1", "Aetna", "PPO")
    review = matcher.write_review_file(tmp_path / "unmatched.csv")
    assert review.read_text().splitlines() == [
        "hospital_id,payer_name,plan_name,price_lines",
        "h1,PFC,PFC,2",
    ]


def test_clean_payer_text() -> None:
    assert clean_payer_text("  CAREFIRST   [201] ") == "carefirst"


def test_every_alias_pattern_compiles_and_ids_are_unique() -> None:
    matcher = PayerMatcher()
    assert len({p.id for p in matcher.payers}) == len(matcher.payers)


# --- text and dates ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ('""Y SET"" TUBING', '"Y SET" TUBING'),  # Novant: doubly escaped quotes
        ("KNEE  MRI\nLEFT ", "KNEE MRI LEFT"),
        ("MRI of brain (no contrast)", "MRI of brain (no contrast)"),
    ],
)
def test_clean_description(raw: str, clean: str) -> None:
    assert clean_description(raw) == clean


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-04-01", "2026-04-01"),  # MedStar
        ("1/1/2026", "2026-01-01"),  # Mercy
        ("6/8/2026", "2026-06-08"),  # UM St. Joseph
        ("04/01/26", "2026-04-01"),
        ("April 2026", None),  # unknown format: a gap, not a guess
        (None, None),
    ],
)
def test_parse_published_date(raw: str | None, expected: str | None) -> None:
    result = parse_published_date(raw)
    assert (result.isoformat() if result else None) == expected


# --- the whole record -------------------------------------------------------------------


def raw_line(**overrides: object) -> RawPriceLine:
    fields: dict[str, object] = dict(
        source_row_number=4,
        source_locator="standard_charge|negotiated_dollar",
        description="MRI KNEE W/O CONTRAST",
        codes=(Code("361", "RC"), Code("73721", "CPT")),
        setting="Outpatient",
        billing_class=None,
        modifiers=None,
        drug_unit=None,
        drug_type=None,
        price_type=PriceType.NEGOTIATED,
        payer_name="Aetna",
        plan_name="PPO",
        dollar="1,234.56",
        percentage=None,
        algorithm=None,
        methodology="fee schedule",
        notes=None,
    )
    fields.update(overrides)
    return RawPriceLine(**fields)  # type: ignore[arg-type]


def test_normalize_line_happy_path(payers: PayerMatcher) -> None:
    line = normalize_line(raw_line(), "h1", payers)
    assert (line.code_type, line.code, line.setting) == (CodeType.CPT, "73721", Setting.OUTPATIENT)
    assert (line.payer_id, line.rate_cents, line.rate_raw) == ("aetna", 123456, "1,234.56")
    assert (line.source_row_number, line.source_locator) == (4, "standard_charge|negotiated_dollar")
    assert line.issues == ()


def test_percentage_only_rate_has_null_cents_not_zero(payers: PayerMatcher) -> None:
    line = normalize_line(raw_line(dollar=None, percentage="92.3"), "h1", payers)
    assert line.rate_cents is None
    assert line.percentage == "92.3"
    assert line.issues == ()


def test_published_empty_negotiated_rate_is_null_and_flagged(payers: PayerMatcher) -> None:
    line = normalize_line(raw_line(dollar="N/A"), "h1", payers)
    assert line.rate_cents is None
    assert "published_as_empty" in line.issues
    assert "negotiated_without_any_rate" in line.issues


def test_gross_price_has_no_payer_and_is_not_flagged_for_it(payers: PayerMatcher) -> None:
    line = normalize_line(
        raw_line(price_type=PriceType.GROSS, payer_name=None, plan_name=None), "h1", payers
    )
    assert line.payer_id is None
    assert "payer_unresolved" not in line.issues


def test_unknown_payer_is_kept_and_flagged(payers: PayerMatcher) -> None:
    line = normalize_line(raw_line(payer_name="PFC", plan_name="PFC"), "h1", payers)
    assert line.payer_id is None
    assert line.payer_name_raw == "PFC"
    assert "payer_unresolved" in line.issues
