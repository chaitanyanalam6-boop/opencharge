"""The one record shape every parser yields, whatever the file looked like.

A RawPriceLine is one published price: one item, in one setting, of one price type, for one
payer/plan when the type is negotiated. Values are kept exactly as the hospital wrote them
(as strings); turning them into cents, canonical payers or code types is normalize/'s job.

Parsers only emit a line when the hospital wrote something for it. A blank cell is absence,
not a price. A cell holding "N/A" or "-" *is* emitted, so normalize can record it as
"published but empty" (NULL rate), which the UI shows as "not published".
"""

from dataclasses import dataclass
from enum import StrEnum


class PriceType(StrEnum):
    GROSS = "gross"
    CASH = "cash"
    NEGOTIATED = "negotiated"
    MIN = "min"
    MAX = "max"


@dataclass(frozen=True, slots=True)
class Code:
    code: str
    type: str  # as declared by the hospital: "CPT", "MS-DRG", "RC", "CDM", ...


@dataclass(frozen=True, slots=True)
class FileHeader:
    """File-level metadata from the CMS template's header block. None = not in the file."""

    hospital_name: str | None
    last_updated_on: str | None
    version: str | None
    location_name: str | None
    hospital_address: str | None


@dataclass(frozen=True, slots=True)
class RawPriceLine:
    # Provenance. CSV: the 1-based row a spreadsheet shows (row 4 is the first data row).
    # JSON: the 1-based position of the item in standard_charge_information.
    source_row_number: int
    # Exactly where in that row/item the price sits. CSV: the column name. JSON: an RFC 6901
    # pointer such as /standard_charge_information/0/standard_charges/0/gross_charge
    source_locator: str

    description: str
    codes: tuple[Code, ...]
    setting: str | None
    billing_class: str | None
    modifiers: str | None
    drug_unit: str | None
    drug_type: str | None

    price_type: PriceType
    payer_name: str | None
    plan_name: str | None
    # The published value(s). For negotiated rates a hospital may give a dollar amount, a
    # percentage, or an algorithm in words; any of them may be the only one present.
    dollar: str | None
    percentage: str | None
    algorithm: str | None
    methodology: str | None
    notes: str | None


@dataclass(frozen=True, slots=True)
class RejectedRow:
    """A row the parser could not read safely. Phase 3 moves these to quarantined_row.

    Prices are never read from a rejected row: if its cells don't line up with the header,
    any value we picked out could belong to a different column.
    """

    source_row_number: int
    reason: str
    raw_payload: tuple[str, ...]
