"""Parsers: fast tests on CMS's official samples plus hand-built messy files."""

import gzip
import io
import json
import zipfile
from decimal import Decimal
from pathlib import Path

import pytest

from opencharge.models import Compression
from opencharge.parse import PARSER_NAMES, get_parser
from opencharge.parse.json_v3 import text
from opencharge.parse.provenance import raw_csv_row, raw_json_item, resolve_json_pointer
from opencharge.parse.records import PriceType, RawPriceLine, RejectedRow

CMS = Path(__file__).parent / "fixtures" / "cms"
TALL = CMS / "V3.0.0_Tall_CSV_Format_Example.csv"
WIDE = CMS / "V3.0.0_Wide_CSV_Format_Example.csv"
JSON = CMS / "v3_json_format_example.json"
SAMPLES = [("cms_csv_tall_v3", TALL), ("cms_csv_wide_v3", WIDE), ("cms_json_v3", JSON)]


def price_lines(
    parser_name: str, path: Path, compression: Compression = Compression.NONE
) -> list[RawPriceLine]:
    return [
        x for x in get_parser(parser_name, path, compression).lines() if isinstance(x, RawPriceLine)
    ]


def comparable(line: RawPriceLine) -> tuple[object, ...]:
    """Everything that should match across formats (not provenance, which differs by design)."""
    return (
        line.description,
        line.codes,
        line.setting,
        line.price_type,
        line.payer_name,
        line.plan_name,
        line.dollar,
        line.percentage,
        line.algorithm,
    )


# --- the three shapes agree -------------------------------------------------------------


def test_all_three_cms_samples_parse_to_the_same_prices() -> None:
    tall, wide, js = ({comparable(x) for x in price_lines(n, p)} for n, p in SAMPLES)
    assert wide == js
    # CMS's own tall sample has one inconsistency: "Basic metabolic panel" gives min=150 on
    # row 12 and min=125 on row 13 (the wide and JSON samples say 125). We report both,
    # faithfully; validation (Phase 3) is where contradictions get flagged.
    [extra] = tall - wide
    assert extra[0] == "Basic metabolic panel" and extra[3] is PriceType.MIN and extra[6] == "150"
    assert wide <= tall


@pytest.mark.parametrize(("parser_name", "path"), SAMPLES)
def test_every_sample_header_is_read(parser_name: str, path: Path) -> None:
    header = get_parser(parser_name, path).read_header()
    assert header.hospital_name == "West Mercy Hospital"
    assert header.version == "3.0.0"
    assert header.last_updated_on is not None


def test_parser_names_match_registry_ids() -> None:
    assert set(PARSER_NAMES) == {"cms_csv_tall_v3", "cms_csv_wide_v3", "cms_json_v3"}


# --- provenance points at the real cell -------------------------------------------------


@pytest.mark.parametrize(("parser_name", "path"), SAMPLES[:2])
def test_csv_provenance_points_at_the_cell_holding_the_value(parser_name: str, path: Path) -> None:
    for line in price_lines(parser_name, path):
        row = raw_csv_row(path, line.source_row_number)
        assert row[line.source_locator].strip() in (line.dollar, line.percentage, line.algorithm)
        assert row["description"] == line.description


def test_json_provenance_points_at_the_value() -> None:
    for line in price_lines("cms_json_v3", JSON):
        item = raw_json_item(JSON, line.source_row_number)
        assert text(resolve_json_pointer(item, line.source_locator)) in (
            line.dollar,
            line.percentage,
            line.algorithm,
        )


# --- specific behaviour on the samples --------------------------------------------------


def test_wide_payer_and_plan_come_from_the_column_header() -> None:
    mri = [x for x in price_lines("cms_csv_wide_v3", WIDE) if x.description.startswith("MRI")]
    negotiated = {
        (x.payer_name, x.plan_name, x.dollar) for x in mri if x.price_type is PriceType.NEGOTIATED
    }
    assert negotiated == {
        ("Platform Health Insurance", "PPO", "400"),
        ("Region Health Insurance", "HMO", "250"),
    }


def test_tall_repeated_gross_is_emitted_once_on_its_first_row() -> None:
    gross = [
        x
        for x in price_lines("cms_csv_tall_v3", TALL)
        if x.description.startswith("MRI") and x.price_type is PriceType.GROSS
    ]
    assert [(x.source_row_number, x.dollar) for x in gross] == [(4, "1200")]


def test_algorithm_only_rate_is_kept_with_no_dollar() -> None:
    csection = [
        x
        for x in price_lines("cms_json_v3", JSON)
        if x.description.startswith("Cesarean") and x.price_type is PriceType.NEGOTIATED
    ]
    assert csection and all(x.dollar is None and x.algorithm for x in csection)


@pytest.mark.parametrize(("parser_name", "path"), SAMPLES)
def test_modifier_rules_are_not_prices(parser_name: str, path: Path) -> None:
    assert not [x for x in price_lines(parser_name, path) if x.description.startswith("Bilateral")]


# --- messy files built to match what real hospitals publish -----------------------------

META = "hospital_name,last_updated_on,version\nTest Hospital,1/1/2026,3.0.0\n"
TALL_HEADER = (
    ",".join(
        [
            "description",
            "code | 1 ",
            "code | 1 | type",
            "setting",
            "standard_charge | gross",
            "standard_charge | discounted_cash",
            "payer_name",
            "plan_name",
            "standard_charge | negotiated_dollar",
            "standard_charge | negotiated_percentage",
            "standard_charge | negotiated_algorithm",
            "standard_charge | methodology",
            "additional_generic_notes",
        ]
    )
    + "\n"
)


def write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_messy_tall_csv(tmp_path: Path) -> None:
    body = (
        # Row 4: description with an embedded line break, inside quotes.
        '"KNEE MRI\nLEFT",73721,CPT,outpatient,"1,200.00",900,Aetna,PPO,850,,,fee schedule,\n'
        # Row 5: blank negotiated cells for this payer -> no negotiated line.
        "KNEE MRI LEFT 2,73722,CPT,outpatient,1300,,Cigna,HMO,,,,,\n"
        # Row 6: "N/A" is published-but-empty and must be emitted for normalize to see.
        "KNEE MRI LEFT 3,73723,CPT,outpatient,N/A,,,,,,,,\n"
        # Row 7: one cell short -> rejected, never read for prices.
        "BROKEN,1,CPT,outpatient,100,90,Aetna,PPO,80,,,\n"
    )
    data = ("﻿" + META + TALL_HEADER + body).encode("utf-8")
    # Row 8: a Windows-1252 curly quote in an otherwise UTF-8 file.
    data += "X-RAY “CHEST”,71045,CPT,outpatient,250,,,,,,,,\n".encode("cp1252")
    parser = get_parser("cms_csv_tall_v3", write(tmp_path, "messy.csv", data))

    out = list(parser.lines())
    lines = [x for x in out if isinstance(x, RawPriceLine)]
    rejected = [x for x in out if isinstance(x, RejectedRow)]

    # Embedded line break: still one row, so the next record is row 5, not 6.
    assert {x.source_row_number for x in lines} == {4, 5, 6, 8}
    knee = [x for x in lines if x.source_row_number == 4]
    assert {(x.price_type, x.dollar, x.payer_name) for x in knee} == {
        (PriceType.GROSS, "1,200.00", None),  # kept as written; normalize parses money
        (PriceType.CASH, "900", None),
        (PriceType.NEGOTIATED, "850", "Aetna"),
    }
    assert not [
        x for x in lines if x.source_row_number == 5 and x.price_type is PriceType.NEGOTIATED
    ]
    assert [x.dollar for x in lines if x.source_row_number == 6] == ["N/A"]
    assert [r.source_row_number for r in rejected] == [7]
    assert [x.description for x in lines if x.source_row_number == 8] == ["X-RAY “CHEST”"]
    assert parser.quirks["cp1252_fallback_lines"] == 1
    assert parser.quirks["utf8_bom"] == 1
    assert parser.read_header().hospital_name == "Test Hospital"


def test_wide_csv_with_percentage_only_payer(tmp_path: Path) -> None:
    header = (
        "description,code|1,code|1|type,setting,standard_charge|gross,"
        "standard_charge|Aetna|PPO|negotiated_dollar,standard_charge|Aetna|PPO|negotiated_percentage,"
        "standard_charge|Aetna|PPO|methodology,"
        "standard_charge|Cigna|HMO|negotiated_dollar,standard_charge|Cigna|HMO|negotiated_percentage\n"
    )
    row = "CT HEAD,70450,CPT,outpatient,1000,,92.3,percent of total billed charges,,\n"
    lines = price_lines("cms_csv_wide_v3", write(tmp_path, "w.csv", (META + header + row).encode()))
    negotiated = [x for x in lines if x.price_type is PriceType.NEGOTIATED]
    assert [(x.payer_name, x.dollar, x.percentage, x.source_locator) for x in negotiated] == [
        ("Aetna", None, "92.3", "standard_charge|Aetna|PPO|negotiated_percentage")
    ]


def test_json_numbers_keep_their_written_form(tmp_path: Path) -> None:
    doc = {
        "hospital_name": "J",
        "last_updated_on": "2026-01-01",
        "version": "3.0.0",
        "standard_charge_information": [
            {
                "description": "THING",
                "code_information": [{"code": "99213", "type": "CPT"}],
                "standard_charges": [
                    {
                        "setting": "outpatient",
                        "gross_charge": 1e3,
                        "payers_information": [
                            {
                                "payer_name": "Aetna",
                                "plan_name": "PPO",
                                "standard_charge_dollar": 0.10,
                            },
                            {"payer_name": "Cigna", "plan_name": "HMO", "methodology": "other"},
                        ],
                    }
                ],
            }
        ],
    }
    path = write(tmp_path, "j.json", json.dumps(doc).encode())
    parser = get_parser("cms_json_v3", path)
    lines = list(parser.lines())
    assert [(x.price_type, x.dollar) for x in lines if isinstance(x, RawPriceLine)] == [
        (PriceType.GROSS, "1000.0"),
        (PriceType.NEGOTIATED, "0.1"),
    ]
    assert parser.quirks["json_payer_entry_without_rate"] == 1


def test_json_item_without_charges_is_rejected(tmp_path: Path) -> None:
    doc = {"standard_charge_information": [{"description": "NO CHARGES"}]}
    out = list(
        get_parser("cms_json_v3", write(tmp_path, "j.json", json.dumps(doc).encode())).lines()
    )
    assert len(out) == 1 and isinstance(out[0], RejectedRow) and out[0].source_row_number == 1


def test_gzip_and_zip_files_are_unwrapped(tmp_path: Path) -> None:
    raw = TALL.read_bytes()
    gz = write(tmp_path, "a.csv.gz", gzip.compress(raw))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("inner.csv", raw)
    zp = write(tmp_path, "a.zip", buffer.getvalue())

    expected = [comparable(x) for x in price_lines("cms_csv_tall_v3", TALL)]
    assert [comparable(x) for x in price_lines("cms_csv_tall_v3", gz, Compression.GZIP)] == expected
    assert [comparable(x) for x in price_lines("cms_csv_tall_v3", zp, Compression.ZIP)] == expected


def test_text_formats_decimals_without_exponent() -> None:
    assert text(Decimal("1E+3")) == "1000"
    assert text("  ") is None
    assert text(None) is None


def test_unknown_parser_is_an_error() -> None:
    with pytest.raises(ValueError, match="unknown parser"):
        get_parser("xml", JSON)


def test_wide_payer_notes_attach_to_their_payer() -> None:
    # Regression: the plan name at the end of "additional_payer_notes|<payer>|<plan>" used to
    # be lowercased, so notes never matched their payer's rate columns.
    hernia = [
        x
        for x in price_lines("cms_csv_wide_v3", WIDE)
        if x.description == "Inguinal hernia repair" and x.price_type is PriceType.NEGOTIATED
    ]
    notes = {(x.payer_name, x.plan_name): x.notes for x in hernia}
    assert (
        notes[("Region Health Insurance", "HMO")]
        == "This service paid at 140% of the OPPS APC rate."
    )
    assert notes[("Platform Health Insurance", "PPO")] is None


def test_wide_payer_names_containing_pipes(tmp_path: Path) -> None:
    # Seen in George Washington University Hospital's file.
    header = (
        "description,code|1,code|1|type,setting,standard_charge|gross,"
        "standard_charge|AETNA | MANAGED_CARE|negotiated_dollar,"
        "standard_charge|AMERIHEALTH | ALLIANCE|MEDICAID|negotiated_dollar,"
        "additional_payer_notes|AMERIHEALTH | ALLIANCE|MEDICAID\n"
    )
    row = "CT HEAD,70450,CPT,outpatient,1000,410.5,388,paid per fee schedule\n"
    parser = get_parser(
        "cms_csv_wide_v3", write(tmp_path, "gwu.csv", (META + header + row).encode())
    )
    negotiated = [
        (x.payer_name, x.plan_name, x.dollar, x.notes, x.source_locator)
        for x in parser.lines()
        if isinstance(x, RawPriceLine) and x.price_type is PriceType.NEGOTIATED
    ]
    assert negotiated == [
        (
            "AETNA",
            "MANAGED_CARE",
            "410.5",
            None,
            "standard_charge|AETNA|MANAGED_CARE|negotiated_dollar",
        ),
        (
            "AMERIHEALTH",
            "ALLIANCE|MEDICAID",
            "388",
            "paid per fee schedule",
            "standard_charge|AMERIHEALTH|ALLIANCE|MEDICAID|negotiated_dollar",
        ),
    ]
    assert parser.quirks["payer_plan_header_ambiguous"] == 2  # the rate column and its notes
