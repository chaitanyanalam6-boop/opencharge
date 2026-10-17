from pathlib import Path

import pytest
from pydantic import ValidationError

from opencharge.registry import FileFormat, load_registry


def test_real_registry_loads() -> None:
    registry = load_registry()
    assert len(registry.hospitals) >= 3


def test_registry_covers_all_three_cms_shapes() -> None:
    formats = {h.format for h in load_registry().hospitals}
    assert formats == {FileFormat.CSV_WIDE, FileFormat.CSV_TALL, FileFormat.JSON}


def test_novant_is_out_of_service_area_and_md_hospitals_are_in() -> None:
    by_id = {h.id: h for h in load_registry().hospitals}
    assert by_id["novant-charlotte-orthopedic"].in_service_area is False
    assert by_id["mercy-medical-center"].in_service_area is True
    assert by_id["medstar-union-memorial"].in_service_area is True


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "hospitals.yaml"
    path.write_text(body, encoding="utf-8")
    return path


MINIMAL_ENTRY = """
hospitals:
  - id: test-hospital
    name: Test Hospital
    ccn: "210001"
    address: {street: 1 Main St, city: Baltimore, state: MD, zip: "21201"}
    lat: 39.29
    lon: -76.61
    type: Acute Care Hospitals
    format: csv_tall
    parser: cms_csv_tall_v3
    mrf_url: https://example.org/file.csv
    discovery:
      method: manual
      source_page_url: https://example.org/prices
"""


def test_in_service_area_defaults_to_true(tmp_path: Path) -> None:
    registry = load_registry(_write(tmp_path, MINIMAL_ENTRY))
    assert registry.hospitals[0].in_service_area is True


def test_misspelled_field_is_rejected_not_defaulted(tmp_path: Path) -> None:
    body = MINIMAL_ENTRY.replace("format: csv_tall", "format: csv_tall\n    in_servce_area: false")
    with pytest.raises(ValidationError, match="in_servce_area"):
        load_registry(_write(tmp_path, body))


def test_xml_is_not_an_accepted_format(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        load_registry(_write(tmp_path, MINIMAL_ENTRY.replace("csv_tall", "xml", 1)))


def test_bad_ccn_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        load_registry(_write(tmp_path, MINIMAL_ENTRY.replace('"210001"', '"21-0001"')))


def test_hpt_discovery_requires_index_url(tmp_path: Path) -> None:
    body = MINIMAL_ENTRY.replace("method: manual", "method: cms_hpt_txt")
    with pytest.raises(ValidationError, match="index_url"):
        load_registry(_write(tmp_path, body))


def test_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    entry = MINIMAL_ENTRY.split("hospitals:\n", 1)[1]
    with pytest.raises(ValidationError, match="duplicate"):
        load_registry(_write(tmp_path, "hospitals:\n" + entry + entry))
