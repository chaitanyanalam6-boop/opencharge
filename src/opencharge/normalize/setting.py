"""Care setting: inpatient, outpatient, both, or unknown."""

import re
from enum import StrEnum


class Setting(StrEnum):
    INPATIENT = "inpatient"
    OUTPATIENT = "outpatient"
    BOTH = "both"
    UNKNOWN = "unknown"


_VALUES = {
    "inpatient": Setting.INPATIENT,
    "ip": Setting.INPATIENT,
    "i": Setting.INPATIENT,
    "outpatient": Setting.OUTPATIENT,
    "op": Setting.OUTPATIENT,
    "o": Setting.OUTPATIENT,
    "both": Setting.BOTH,
    "all": Setting.BOTH,
    "inpatient/outpatient": Setting.BOTH,
    "inpatient and outpatient": Setting.BOTH,
    "ip/op": Setting.BOTH,
}


def normalize_setting(value: str | None, column: str | None = None) -> tuple[Setting, str | None]:
    """Return (setting, issue or None).

    The setting column wins. If it is empty, a column name like
    "standard_charge|Aetna|PPO Inpatient|negotiated_dollar" can still say which setting a
    rate applies to; otherwise the setting is unknown (never guessed).
    """
    if value is not None:
        key = re.sub(r"\s+", " ", value.strip().lower())
        if key in _VALUES:
            return _VALUES[key], None
        return Setting.UNKNOWN, f"unrecognized_setting:{value.strip()}"

    if column:
        words = set(re.findall(r"[a-z]+", column.lower()))
        has_in, has_out = "inpatient" in words, "outpatient" in words
        if has_in and not has_out:
            return Setting.INPATIENT, None
        if has_out and not has_in:
            return Setting.OUTPATIENT, None
    return Setting.UNKNOWN, None
