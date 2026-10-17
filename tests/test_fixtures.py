"""Guard the CMS sample files the fast parser tests will depend on.

Source: github.com/CMSgov/hospital-price-transparency, commit 5333564a, examples/.
"""

import csv
import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "cms"


def test_cms_json_sample_is_v3_with_items() -> None:
    data = json.loads((FIXTURES / "v3_json_format_example.json").read_text(encoding="utf-8"))
    assert data["version"] == "3.0.0"
    assert len(data["standard_charge_information"]) > 0


def test_cms_csv_samples_have_header_on_third_row() -> None:
    # CMS v3 CSVs: row 1 = metadata keys, row 2 = metadata values, row 3 = column header.
    for name in ("V3.0.0_Tall_CSV_Format_Example.csv", "V3.0.0_Wide_CSV_Format_Example.csv"):
        with (FIXTURES / name).open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        assert rows[2][0] == "description", name
