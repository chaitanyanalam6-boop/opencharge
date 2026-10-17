"""Load and validate sources/hospitals.yaml.

The registry is hand-edited, so it is validated strictly: a typo in a field name or a
malformed CCN fails loudly at load time instead of surfacing as a bad row three phases later.
"""

from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from opencharge import PROJECT_ROOT

DEFAULT_REGISTRY_PATH = PROJECT_ROOT / "sources" / "hospitals.yaml"


class FileFormat(StrEnum):
    """The three CMS template shapes. XML is not a CMS template, so it is not listed."""

    CSV_WIDE = "csv_wide"
    CSV_TALL = "csv_tall"
    JSON = "json"


class DiscoveryMethod(StrEnum):
    CMS_HPT_TXT = "cms_hpt_txt"
    MANUAL = "manual"


class _Strict(BaseModel):
    # Reject unknown keys so a misspelled field ("in_servce_area") is an error, not a default.
    model_config = ConfigDict(extra="forbid", frozen=True)


class Address(_Strict):
    street: str
    city: str
    state: str = Field(pattern=r"^[A-Z]{2}$")
    zip: str = Field(pattern=r"^\d{5}$")


class Discovery(_Strict):
    method: DiscoveryMethod
    index_url: HttpUrl | None = None
    source_page_url: HttpUrl

    @model_validator(mode="after")
    def index_required_for_hpt(self) -> "Discovery":
        if self.method is DiscoveryMethod.CMS_HPT_TXT and self.index_url is None:
            raise ValueError("discovery.index_url is required when method is cms_hpt_txt")
        return self


class HospitalEntry(_Strict):
    id: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    name: str
    ccn: str = Field(pattern=r"^\d{6}$")
    address: Address
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    type: str
    in_service_area: bool = True
    format: FileFormat
    parser: str
    mrf_url: HttpUrl
    discovery: Discovery
    notes: str | None = None


class Registry(_Strict):
    hospitals: list[HospitalEntry]

    @model_validator(mode="after")
    def ids_unique(self) -> "Registry":
        ids = [h.id for h in self.hospitals]
        duplicates = {i for i in ids if ids.count(i) > 1}
        if duplicates:
            raise ValueError(f"duplicate hospital ids: {sorted(duplicates)}")
        return self


def load_registry(path: Path = DEFAULT_REGISTRY_PATH) -> Registry:
    """Parse and validate the registry file. Raises pydantic.ValidationError on bad input."""
    with path.open(encoding="utf-8") as f:
        return Registry.model_validate(yaml.safe_load(f))
