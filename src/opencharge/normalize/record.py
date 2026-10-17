"""RawPriceLine -> NormalizedPriceLine: the shape price_line rows will be loaded from.

Normalization never drops a line. Anything odd becomes a named entry in `issues`, and
Phase 3's validation rules decide what is quarantined.
"""

from dataclasses import dataclass

from opencharge.normalize.codes import CodeType, primary_code
from opencharge.normalize.money import parse_money, parse_percentage
from opencharge.normalize.payers import PayerMatcher
from opencharge.normalize.setting import Setting, normalize_setting
from opencharge.normalize.text import clean_description
from opencharge.parse.records import PriceType, RawPriceLine


@dataclass(frozen=True, slots=True)
class NormalizedPriceLine:
    hospital_id: str
    code_type: CodeType | None
    code: str | None
    other_codes: tuple[tuple[CodeType, str], ...]
    description_raw: str
    description_clean: str
    setting: Setting
    price_type: PriceType
    payer_id: str | None
    payer_name_raw: str | None
    plan_name: str | None
    # NULL means "published but empty" (or no dollar figure, e.g. percentage-only). Never 0.
    rate_cents: int | None
    rate_raw: str | None
    percentage: str | None
    algorithm: str | None
    methodology: str | None
    billing_class: str | None
    modifiers: str | None
    drug_unit: str | None
    drug_type: str | None
    notes: str | None
    source_row_number: int
    source_locator: str
    issues: tuple[str, ...]


def normalize_line(
    raw: RawPriceLine, hospital_id: str, payers: PayerMatcher
) -> NormalizedPriceLine:
    issues: list[str] = []

    primary, others, code_issues = primary_code(raw.codes)
    issues.extend(code_issues)

    setting, setting_issue = normalize_setting(raw.setting, raw.source_locator)
    if setting_issue:
        issues.append(setting_issue)

    money = parse_money(raw.dollar)
    if money.issue:
        issues.append(money.issue)

    percentage = parse_percentage(raw.percentage)
    if raw.percentage is not None and percentage is None:
        issues.append("unparseable_percentage")

    payer_id = None
    if raw.price_type is PriceType.NEGOTIATED:
        payer_id = payers.resolve(hospital_id, raw.payer_name, raw.plan_name)
        if payer_id is None:
            issues.append("payer_unresolved")
        if money.cents is None and percentage is None and raw.algorithm is None:
            issues.append("negotiated_without_any_rate")

    return NormalizedPriceLine(
        hospital_id=hospital_id,
        code_type=primary[0] if primary else None,
        code=primary[1] if primary else None,
        other_codes=others,
        description_raw=raw.description,
        description_clean=clean_description(raw.description),
        setting=setting,
        price_type=raw.price_type,
        payer_id=payer_id,
        payer_name_raw=raw.payer_name,
        plan_name=raw.plan_name,
        rate_cents=money.cents,
        rate_raw=raw.dollar,
        percentage=format(percentage, "f") if percentage is not None else None,
        algorithm=raw.algorithm,
        methodology=raw.methodology,
        billing_class=raw.billing_class,
        modifiers=raw.modifiers,
        drug_unit=raw.drug_unit,
        drug_type=raw.drug_type,
        notes=raw.notes,
        source_row_number=raw.source_row_number,
        source_locator=raw.source_locator,
        issues=tuple(issues),
    )
