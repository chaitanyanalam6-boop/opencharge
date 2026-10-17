"""CMS v3 JSON: nested items, each holding standard_charges, each holding payers_information.

ijson walks the file as a stream of events, so only one item is in memory at a time.
"""

from collections import Counter
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import ijson

from opencharge.models import Compression
from opencharge.parse._io import open_binary
from opencharge.parse.dedupe import FirstSeen
from opencharge.parse.records import Code, FileHeader, PriceType, RawPriceLine, RejectedRow

ITEMS_PREFIX = "standard_charge_information.item"
_ITEM_PRICES = {
    "gross_charge": PriceType.GROSS,
    "discounted_cash": PriceType.CASH,
    "minimum": PriceType.MIN,
    "maximum": PriceType.MAX,
}
_HEADER_FIELDS = (
    "hospital_name",
    "last_updated_on",
    "version",
    "location_name",
    "hospital_address",
)


def text(value: Any) -> str | None:
    """A JSON scalar as the string the hospital wrote, or None if absent/blank.

    Numbers arrive as Decimal (never float, so no binary rounding); format 'f' avoids
    turning 1000 into '1E+3'.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    result = str(value).strip()
    return result or None


class JsonV3Parser:
    name = "cms_json_v3"

    def __init__(self, path: Path, compression: Compression = Compression.NONE) -> None:
        self.path = path
        self.compression = compression
        self.quirks: Counter[str] = Counter()

    def read_header(self) -> FileHeader:
        """Read top-level fields that appear before the item array.

        Hospitals almost always write them first. If one comes after the array we report it
        as missing rather than read 300 MB to find it.
        """
        found: dict[str, str] = {}
        with open_binary(self.path, self.compression) as stream:
            for prefix, event, value in ijson.parse(stream, use_float=False):
                if prefix == "standard_charge_information" and event == "start_array":
                    break
                # location_name and hospital_address are arrays in v3; keep the first entry.
                field = prefix.removesuffix(".item")
                if field in _HEADER_FIELDS and event in ("string", "number") and field not in found:
                    found[field] = text(value) or ""
        return FileHeader(**{f: found.get(f) or None for f in _HEADER_FIELDS})

    def lines(self) -> Iterator[RawPriceLine | RejectedRow]:
        first_seen = FirstSeen(self.quirks)
        with open_binary(self.path, self.compression) as stream:
            items = ijson.items(stream, ITEMS_PREFIX, use_float=False)
            for item_number, item in enumerate(items, start=1):
                yield from self._item_lines(item_number, item, first_seen)

    def _item_lines(
        self, item_number: int, item: Any, first_seen: FirstSeen
    ) -> Iterator[RawPriceLine | RejectedRow]:
        base = f"/standard_charge_information/{item_number - 1}"
        if not isinstance(item, dict) or not isinstance(item.get("standard_charges"), list):
            yield RejectedRow(
                item_number, "item has no standard_charges array", (repr(item)[:2000],)
            )
            return

        drug = item.get("drug_information") or {}
        codes = tuple(
            Code(text(c.get("code")) or "", text(c.get("type")) or "")
            for c in item.get("code_information") or []
            if isinstance(c, dict) and text(c.get("code"))
        )

        for charge_index, charge in enumerate(item["standard_charges"]):
            charge_path = f"{base}/standard_charges/{charge_index}"
            common = dict(
                description=text(item.get("description")) or "",
                codes=codes,
                setting=text(charge.get("setting")),
                billing_class=text(charge.get("billing_class")),
                modifiers=", ".join(text(m) or "" for m in charge.get("modifier_code") or [])
                or None,
                drug_unit=text(drug.get("unit")),
                drug_type=text(drug.get("type")),
            )
            for field, price_type in _ITEM_PRICES.items():
                value = text(charge.get(field))
                if value is not None and first_seen.is_first(common, price_type, value):
                    yield RawPriceLine(
                        source_row_number=item_number,
                        source_locator=f"{charge_path}/{field}",
                        price_type=price_type,
                        payer_name=None,
                        plan_name=None,
                        dollar=value,
                        percentage=None,
                        algorithm=None,
                        methodology=None,
                        notes=text(charge.get("additional_generic_notes")),
                        **common,  # type: ignore[arg-type]
                    )

            for payer_index, payer in enumerate(charge.get("payers_information") or []):
                dollar = text(payer.get("standard_charge_dollar"))
                percentage = text(payer.get("standard_charge_percentage"))
                algorithm = text(payer.get("standard_charge_algorithm"))
                if dollar is None and percentage is None and algorithm is None:
                    self.quirks["json_payer_entry_without_rate"] += 1
                    continue
                field = (
                    "standard_charge_dollar"
                    if dollar is not None
                    else (
                        "standard_charge_percentage"
                        if percentage is not None
                        else "standard_charge_algorithm"
                    )
                )
                yield RawPriceLine(
                    source_row_number=item_number,
                    source_locator=f"{charge_path}/payers_information/{payer_index}/{field}",
                    price_type=PriceType.NEGOTIATED,
                    payer_name=text(payer.get("payer_name")),
                    plan_name=text(payer.get("plan_name")),
                    dollar=dollar,
                    percentage=percentage,
                    algorithm=algorithm,
                    methodology=text(payer.get("methodology")),
                    notes=text(payer.get("additional_payer_notes")),
                    **common,  # type: ignore[arg-type]
                )
