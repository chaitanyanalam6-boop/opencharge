"""Natural-language question -> a validated, structured search. Never a price, never SQL.

Two LLM calls, with plain retrieval in between:

  1. extract   question -> filters (payer, setting, price types, ZIP, radius) and search
               terms written the way hospitals write descriptions. No code, no prices.
  2. retrieve  search terms -> shortlist of real codes from the catalog (no LLM).
  3. choose    question + shortlist -> one code from the shortlist (or none) + confidence.

Every reply is validated by a strict Pydantic model: unknown fields are rejected, payers must
be canonical ids we hold, codes must be on the shortlist, and no string may contain a dollar
sign. A reply that fails validation is retried once with the error; a second failure raises
InterpretationError with a message safe to show the user. Free text never gets through.

The LLM is never shown a price (the shortlist carries descriptions and hospital counts
only), so it has nothing to repeat, round or average.
"""

import json
import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, field_validator

from opencharge.query.catalog import Candidate, CodeCatalog
from opencharge.query.geo import locate_zip
from opencharge.query.llm import LLMClient

log = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.7
DEFAULT_RADIUS_MILES = 25
PriceTypeName = Literal["gross", "cash", "negotiated", "min", "max"]
DEFAULT_PRICE_TYPES: tuple[PriceTypeName, ...] = ("negotiated", "cash", "gross")
SettingName = Literal["inpatient", "outpatient", "both"]
CodeTypeName = Literal["CPT", "HCPCS", "DRG"]


class InterpretationError(Exception):
    """The question could not be turned into a valid search. The message is user-safe."""


def _no_money(value: str) -> str:
    if "$" in value:
        raise ValueError("must not contain a dollar amount")
    return value


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Extraction(_Strict):
    """Stage 1: everything except the code."""

    description_terms: list[str] = Field(min_length=1, max_length=12)
    payer: str | None = None
    setting: SettingName | None = None
    price_types: list[PriceTypeName] = Field(
        default_factory=lambda: list(DEFAULT_PRICE_TYPES), min_length=1
    )
    near_zip: str | None = Field(default=None, pattern=r"^\d{5}$")
    radius_miles: int = Field(default=DEFAULT_RADIUS_MILES, ge=1, le=100)
    unresolved: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("description_terms")
    @classmethod
    def terms_are_words(cls, terms: list[str]) -> list[str]:
        for term in terms:
            if not term or len(term) > 40 or not all(c.isalnum() or c in " -/" for c in term):
                raise ValueError(
                    f"search term {term!r} must be 1-40 letters, digits, spaces, - or /"
                )
        return [t.lower() for t in terms]

    @field_validator("payer")
    @classmethod
    def payer_is_known(cls, payer: str | None, info: ValidationInfo) -> str | None:
        known = (info.context or {}).get("payer_ids", set())
        if payer is not None and payer not in known:
            raise ValueError(f"payer must be one of the listed ids or null, got {payer!r}")
        return payer

    @field_validator("unresolved")
    @classmethod
    def unresolved_is_short_text(cls, items: list[str]) -> list[str]:
        for item in items:
            if len(item) > 120:
                raise ValueError("each unresolved item must be at most 120 characters")
            _no_money(item)
        return items


class CodeSelection(_Strict):
    """Stage 2: one code from the shortlist, or none."""

    code_type: CodeTypeName | None
    code: str | None
    confidence: float = Field(ge=0.0, le=1.0)
    unresolved: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("code")
    @classmethod
    def code_is_on_the_shortlist(cls, code: str | None, info: ValidationInfo) -> str | None:
        shortlist = (info.context or {}).get("shortlist", set())
        code_type = info.data.get("code_type")
        if code is None and code_type is None:
            return None
        if (code_type, code) not in shortlist:
            raise ValueError(
                f"({code_type}, {code}) is not one of the candidate codes; "
                "choose from the list or use null"
            )
        return code

    @field_validator("unresolved")
    @classmethod
    def unresolved_is_short_text(cls, items: list[str]) -> list[str]:
        return Extraction.unresolved_is_short_text(items)


class Interpretation(_Strict):
    """The structured search (brief §5, Phase 4). This, and only this, reaches the executor."""

    code_type: CodeTypeName | None
    # Defence in depth: a code is a short token of letters, digits and hyphens.
    code: str | None = Field(pattern=r"^[A-Z0-9-]{1,12}$")
    description_terms: list[str]
    payer: str | None
    setting: SettingName | None
    price_types: list[PriceTypeName]
    near_zip: str | None
    radius_miles: int
    confidence: float
    unresolved: list[str]

    @property
    def needs_confirmation(self) -> bool:
        """Ask the user before searching, rather than guess (brief §5, Phase 4)."""
        return self.code is None or self.confidence < CONFIDENCE_THRESHOLD or bool(self.unresolved)


EXTRACT_SYSTEM = """You turn a question about hospital prices into search filters.
Reply with one JSON object and nothing else, with exactly these keys:
  "description_terms": 3-10 short search terms for the procedure, written the way hospital
      price files describe it: include the plain words AND common clinical synonyms
      (e.g. "knee MRI" -> ["mri", "knee", "lower extremity", "joint"]). Lowercase words only.
  "payer": one id from the PAYERS list below, or null if no insurer was named.
  "setting": "inpatient", "outpatient", "both", or null if not stated.
  "price_types": a list from ["negotiated", "cash", "gross", "min", "max"]. If an insurer is
      named use ["negotiated", "cash"]; if the person is uninsured or paying themselves use
      ["cash"]; otherwise ["negotiated", "cash", "gross"].
  "near_zip": a 5-digit ZIP code if one was given or the place clearly maps to one
      (Baltimore -> "21201", Washington DC -> "20001", Towson -> "21204"), else null.
  "radius_miles": an integer 1-100; 25 unless stated.
  "unresolved": a list of short notes for anything in the question you could not map,
      e.g. an insurer not in the list ("payer: Bright Health"), or [] if none.
Rules: never output a price, a dollar amount, a billing code, or SQL. Only the keys above.
PAYERS:
{payers}"""

CHOOSE_SYSTEM = """You pick which billing code a question is about, from a fixed list of candidates.
Reply with one JSON object and nothing else, with exactly these keys:
  "code_type": the code_type of your chosen candidate, or null.
  "code": the code of your chosen candidate, exactly as listed, or null if none fits.
  "confidence": a number from 0 to 1 for how sure you are the candidate matches the question.
  "unresolved": short notes on any ambiguity, e.g. ["with or without contrast not stated"],
      or [] if none.
Rules: choose only from CANDIDATES; never invent a code. Never output a price, a dollar
amount, or SQL. If no candidate fits, use null for code and code_type and confidence 0."""


def _ask[Model: BaseModel](
    llm: LLMClient, system: str, user: str, model: type[Model], context: dict[str, Any]
) -> Model:
    """Call the LLM and validate; on failure, retry once with the error, then give up."""
    reply = llm.complete_json(system, user)
    try:
        return model.model_validate(json.loads(reply), context=context)
    except (json.JSONDecodeError, ValidationError) as first_error:
        log.info("LLM reply rejected, retrying once: %s", first_error)
        retry_user = (
            f"{user}\n\nYour previous reply was rejected:\n{first_error}\n"
            "Reply again with a corrected JSON object only."
        )
        reply = llm.complete_json(system, retry_user)
        try:
            return model.model_validate(json.loads(reply), context=context)
        except (json.JSONDecodeError, ValidationError) as second_error:
            log.warning("LLM reply rejected twice: %s", second_error)
            raise InterpretationError(
                "Sorry, I couldn't turn that question into a search. Try naming the procedure "
                "and, if you like, an insurer and a ZIP code."
            ) from second_error


def interpret(
    question: str, llm: LLMClient, catalog: CodeCatalog, payers: dict[str, str]
) -> Interpretation:
    """Turn a question into a validated Interpretation. Raises InterpretationError."""
    question = question.strip()
    if not question or len(question) > 300:
        raise InterpretationError("Please ask a question between 1 and 300 characters.")

    payer_list = "\n".join(f"  {pid}: {name}" for pid, name in sorted(payers.items()))
    extraction = _ask(
        llm,
        EXTRACT_SYSTEM.format(payers=payer_list),
        f"QUESTION: {question}",
        Extraction,
        {"payer_ids": set(payers)},
    )
    unresolved = list(extraction.unresolved)
    if extraction.near_zip and locate_zip(extraction.near_zip) is None:
        unresolved.append(f"ZIP {extraction.near_zip} is outside the area we cover")

    candidates = catalog.search(extraction.description_terms + [question])
    if not candidates:
        selection = CodeSelection(
            code_type=None, code=None, confidence=0.0, unresolved=["no matching procedure found"]
        )
    else:
        selection = _ask(
            llm,
            CHOOSE_SYSTEM,
            f"QUESTION: {question}\n\nCANDIDATES:\n{format_candidates(candidates)}",
            CodeSelection,
            {"shortlist": {(c.code_type, c.code) for c in candidates}},
        )

    return Interpretation(
        code_type=selection.code_type,
        code=selection.code,
        description_terms=extraction.description_terms,
        payer=extraction.payer,
        setting=extraction.setting,
        price_types=extraction.price_types,
        near_zip=extraction.near_zip,
        radius_miles=extraction.radius_miles,
        confidence=selection.confidence,
        unresolved=unresolved + selection.unresolved,
    )


def format_candidates(candidates: list[Candidate]) -> str:
    """The shortlist as the LLM sees it: codes and descriptions, never prices."""
    return "\n".join(
        json.dumps(
            {
                "code_type": c.code_type,
                "code": c.code,
                "hospital_descriptions": list(c.descriptions),
                "hospitals_publishing": c.hospital_count,
            }
        )
        for c in candidates
    )
