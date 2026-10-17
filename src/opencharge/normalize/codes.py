"""Billing code types.

The hospital declares a type for each code ("CPT", "MS-DRG", "RC", ...). We trust the
declaration and check it against the code's shape. Where nothing is declared we fall back
to shape alone (5-digit numeric -> CPT, letter + 4 digits -> HCPCS, 3 digits -> DRG).
"""

import re
from enum import StrEnum

from opencharge.parse.records import Code


class CodeType(StrEnum):
    CPT = "CPT"
    HCPCS = "HCPCS"
    DRG = "DRG"
    NDC = "NDC"
    LOCAL = "LOCAL"
    REV = "REV"


# CPT: 5 characters — 5 digits, or 4 digits + a letter for Category II (F), Category III
# (T), proprietary lab (U), multianalyte assay (M) and vaccine administration (A) codes.
_CPT = re.compile(r"^\d{4}[0-9FTUMA]$")
# The same codes after a spreadsheet stripped their leading zeros: "123U" was "0123U",
# anesthesia code "100" was "00100".
_CPT_ZEROS_LOST = re.compile(r"^\d{1,3}[0-9FTUMA]$")
# HCPCS Level II: a letter A–V followed by 4 digits (J1100, G0378).
_HCPCS = re.compile(r"^[A-V]\d{4}$")
_DRG = re.compile(r"^\d{1,3}$")
_REV = re.compile(r"^\d{3,4}$")
_NDC = re.compile(r"^(\d{11}|\d{4,5}-\d{3,4}-\d{1,2})$")

_DECLARED: dict[str, CodeType] = {
    "CPT": CodeType.CPT,
    "HCPCS": CodeType.HCPCS,
    "NDC": CodeType.NDC,
    "RC": CodeType.REV,
    "CDM": CodeType.LOCAL,
    "LOCAL": CodeType.LOCAL,
}
# CMS lists several DRG families; for search they are all DRGs.
_DRG_FAMILIES = {"DRG", "MS-DRG", "APR-DRG", "AP-DRG", "R-DRG", "S-DRG", "APS-DRG", "TRIS-DRG"}

# When an item has several codes, the one a person would search by wins.
PRIORITY = (CodeType.CPT, CodeType.HCPCS, CodeType.DRG, CodeType.NDC, CodeType.REV, CodeType.LOCAL)


def classify(code: str, declared: str) -> tuple[CodeType, str, str | None]:
    """Return (code type, normalized code, issue or None)."""
    value = code.strip().upper()
    kind = declared.strip().upper()

    # CPT is HCPCS Level I, so hospitals use the two labels interchangeably. Shape decides.
    if kind in ("CPT", "HCPCS"):
        if _CPT.match(value):
            return CodeType.CPT, value, None
        if _HCPCS.match(value):
            return CodeType.HCPCS, value, None
        if kind == "CPT" and _CPT_ZEROS_LOST.match(value):
            # CPT codes are always exactly 5 characters, so padding restores the code
            # deterministically; it is flagged so the raw value stays visible.
            return CodeType.CPT, value.zfill(5), "leading_zeros_restored"
        return _DECLARED[kind], value, "code_shape_mismatch"

    if kind in _DRG_FAMILIES:
        if _DRG.match(value):
            return CodeType.DRG, value.zfill(3), None
        return CodeType.DRG, value, "code_shape_mismatch"

    if kind == "RC":
        # Revenue codes are 4 digits; many files drop the leading zero ("361" for "0361").
        if _REV.match(value):
            return CodeType.REV, value.zfill(4), None
        return CodeType.REV, value, "code_shape_mismatch"

    if kind == "NDC":
        return CodeType.NDC, value, None if _NDC.match(value) else "code_shape_mismatch"

    if kind in _DECLARED:
        return _DECLARED[kind], value, None

    # Nothing usable declared (blank, or a type we don't search on such as APC or HIPPS).
    issue = "undeclared_code_type" if not kind else f"unsearched_code_type:{kind}"
    if _CPT.match(value) and value.isdigit():
        return CodeType.CPT, value, issue
    if _HCPCS.match(value):
        return CodeType.HCPCS, value, issue
    if re.fullmatch(r"\d{3}", value):
        return CodeType.DRG, value, issue
    if _NDC.match(value):
        return CodeType.NDC, value, issue
    return CodeType.LOCAL, value, issue


def primary_code(
    codes: tuple[Code, ...],
) -> tuple[tuple[CodeType, str] | None, tuple[tuple[CodeType, str], ...], tuple[str, ...]]:
    """Pick the code a person would search by; keep the rest. Returns (primary, others, issues)."""
    classified = []
    issues = []
    for c in codes:
        if set(c.code.strip()) <= {"0"}:
            # Mercy writes RC "0" on 731 pharmacy rows to mean "no revenue code". A
            # placeholder must never become a searchable code.
            issues.append(f"placeholder_code_ignored ({c.type or 'no type'} {c.code})")
            continue
        code_type, value, issue = classify(c.code, c.type)
        classified.append((code_type, value))
        if issue:
            issues.append(f"{issue} ({c.type or 'no type'} {c.code})")
    if not classified:
        return None, (), (*issues, "no_code")
    best = min(classified, key=lambda pair: PRIORITY.index(pair[0]))
    others = tuple(pair for pair in classified if pair is not best)
    return best, others, tuple(issues)
