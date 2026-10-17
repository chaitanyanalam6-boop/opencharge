"""Text search over the codes hospitals actually publish.

The LLM never recalls codes from memory: it chooses from a shortlist this module retrieves.
The catalog is one entry per searchable code (CPT, HCPCS, MS-DRG) with every description
in-area hospitals use for it. Hospital descriptions are heavily abbreviated ("MRI LWR EXT
JOINT WO CON", "BC-MRI LREX JT WO C LTD KNE LT"), so both descriptions and search terms are
normalized through the same abbreviation table before matching.

Scoring is plain TF-IDF style: a query term that appears in few codes' descriptions (e.g.
"extremity") counts for more than one in many ("hc"), weighted by how consistently the term
appears across that code's descriptions.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import Connection, func, select

from opencharge.models import price_line
from opencharge.query.scope import searchable_hospitals

SEARCHABLE_CODE_TYPES = ("CPT", "HCPCS", "DRG")

# Abbreviations seen in real hospital descriptions -> the words people use.
ABBREVIATIONS: dict[str, tuple[str, ...]] = {
    "wo": ("without",),
    "w": ("with",),
    "con": ("contrast",),
    "cont": ("contrast",),
    "contr": ("contrast",),
    "contrst": ("contrast",),
    "dye": ("contrast",),
    "lwr": ("lower",),
    "low": ("lower",),
    "lrex": ("lower", "extremity"),
    "ext": ("extremity",),
    "extrem": ("extremity",),
    "extre": ("extremity",),
    "extr": ("extremity",),
    "uprex": ("upper", "extremity"),
    "upr": ("upper",),
    "uppr": ("upper",),
    "jt": ("joint",),
    "jnt": ("joint",),
    "kne": ("knee",),
    "abd": ("abdomen",),
    "abdom": ("abdomen",),
    "pelv": ("pelvis",),
    "xr": ("xray",),
    "mr": ("mri",),
    "us": ("ultrasound",),
    "ultrasnd": ("ultrasound",),
    "repl": ("replacement",),
    "arthrp": ("arthroplasty",),
    "tot": ("total",),
    "bilat": ("bilateral",),
    "bil": ("bilateral",),
    "ltd": ("limited",),
    "est": ("established",),
    "estab": ("established",),
    "pt": ("patient",),
    "ofc": ("office",),
    "colonosc": ("colonoscopy",),
    "mammo": ("mammogram",),
}
# Noise in hospital descriptions: department prefixes ("HC", "BC") and laterality.
STOPWORDS = frozenset(
    {
        "hc",
        "bc",
        "lt",
        "rt",
        "of",
        "the",
        "and",
        "or",
        "a",
        "an",
        "for",
        "to",
        "in",
        "on",
        "by",
        "per",
    }
)


def tokenize(text: str) -> list[str]:
    """Lowercase words with abbreviations expanded and noise removed."""
    text = text.lower().replace("w/o", " without ").replace("w/", " with ").replace("x-ray", "xray")
    tokens: list[str] = []
    for word in re.findall(r"[a-z0-9]+", text):
        for token in ABBREVIATIONS.get(word, (word,)):
            if token not in STOPWORDS:
                tokens.append(token)
    return tokens


@dataclass
class CatalogEntry:
    code_type: str
    code: str
    descriptions: Counter[str] = field(default_factory=Counter)
    hospitals: set[str] = field(default_factory=set)
    # token -> how many of this code's descriptions contain it
    token_counts: Counter[str] = field(default_factory=Counter)


@dataclass(frozen=True)
class Candidate:
    code_type: str
    code: str
    descriptions: tuple[str, ...]  # the most common ways hospitals describe it
    hospital_count: int
    score: float


class CodeCatalog:
    def __init__(self, entries: dict[tuple[str, str], CatalogEntry]) -> None:
        self.entries = entries
        documents = Counter[str]()
        for entry in entries.values():
            documents.update(entry.token_counts.keys())
        total = max(len(entries), 1)
        self.idf = {t: math.log(1 + total / n) for t, n in documents.items()}

    @classmethod
    def build(cls, conn: Connection) -> "CodeCatalog":
        """One query over price_line, restricted to hospitals users may see."""
        query = (
            select(
                price_line.c.code_type,
                price_line.c.code,
                price_line.c.description_clean,
                price_line.c.hospital_id,
                func.count().label("n"),
            )
            .where(
                price_line.c.hospital_id.in_(searchable_hospitals()),
                price_line.c.code_type.in_(SEARCHABLE_CODE_TYPES),
            )
            .group_by(
                price_line.c.code_type,
                price_line.c.code,
                price_line.c.description_clean,
                price_line.c.hospital_id,
            )
        )
        entries: dict[tuple[str, str], CatalogEntry] = {}
        for code_type, code, description, hospital_id, n in conn.execute(query):
            entry = entries.setdefault((code_type, code), CatalogEntry(code_type, code))
            entry.hospitals.add(hospital_id)
            if description not in entry.descriptions:
                entry.token_counts.update(set(tokenize(description)))
            entry.descriptions[description] += n
        return cls(entries)

    def search(self, terms: list[str], limit: int = 15) -> list[Candidate]:
        query_tokens = set()
        for term in terms:
            query_tokens.update(tokenize(term))
        # Exact code match: someone asked for "73721" or "J1100" directly.
        direct = [e for (_, code), e in self.entries.items() if code.lower() in query_tokens]

        scored: list[tuple[float, CatalogEntry]] = []
        for entry in self.entries.values():
            variants = len(entry.descriptions)
            score = sum(
                self.idf.get(t, 0.0) * entry.token_counts[t] / variants
                for t in query_tokens
                if t in entry.token_counts
            )
            if score > 0:
                # Small tie-breaker: codes many hospitals publish are more useful answers.
                scored.append((score + 0.1 * math.log1p(len(entry.hospitals)), entry))
        scored.sort(key=lambda pair: pair[0], reverse=True)

        ranked = [(math.inf, e) for e in direct] + [p for p in scored if p[1] not in direct]
        return [
            Candidate(
                e.code_type,
                e.code,
                tuple(d for d, _ in e.descriptions.most_common(3)),
                len(e.hospitals),
                round(s, 3) if s != math.inf else 999.0,
            )
            for s, e in ranked[:limit]
        ]
