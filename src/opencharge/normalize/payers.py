"""Map raw payer names to canonical payers (sources/payer_aliases.yaml).

Unmatched names are counted for human review, never dropped and never guessed.
"""

import csv
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import yaml

from opencharge import PROJECT_ROOT

DEFAULT_ALIASES_PATH = PROJECT_ROOT / "sources" / "payer_aliases.yaml"
DEFAULT_REVIEW_PATH = PROJECT_ROOT / "data" / "review" / "unmatched_payers.csv"

_BRACKETED = re.compile(r"\[[^\]]*\]")


@dataclass(frozen=True)
class CanonicalPayer:
    id: str
    name: str
    patterns: tuple[re.Pattern[str], ...]


def clean_payer_text(text: str) -> str:
    """'CAREFIRST [201]' -> 'carefirst'. Lowercase, bracketed codes removed, spaces collapsed."""
    return re.sub(r"\s+", " ", _BRACKETED.sub(" ", text)).strip().lower()


class PayerMatcher:
    def __init__(self, aliases_path: Path = DEFAULT_ALIASES_PATH) -> None:
        with aliases_path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        self.payers = tuple(
            CanonicalPayer(
                p["id"], p["name"], tuple(re.compile(x, re.IGNORECASE) for x in p["patterns"])
            )
            for p in raw["payers"]
        )
        ids = [p.id for p in self.payers]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate payer ids in {aliases_path}")
        # (hospital_id, raw payer, raw plan) -> number of price lines affected
        self.unmatched: Counter[tuple[str, str, str]] = Counter()

    def match(self, payer_name: str | None, plan_name: str | None) -> str | None:
        if not payer_name:
            return None
        candidates = [clean_payer_text(payer_name)]
        if plan_name:
            candidates.append(clean_payer_text(f"{payer_name} {plan_name}"))
        for text in candidates:
            for payer in self.payers:
                if any(p.search(text) for p in payer.patterns):
                    return payer.id
        return None

    def resolve(
        self, hospital_id: str, payer_name: str | None, plan_name: str | None
    ) -> str | None:
        """Like match(), but records misses for review."""
        payer_id = self.match(payer_name, plan_name)
        if payer_id is None and payer_name:
            self.unmatched[(hospital_id, payer_name, plan_name or "")] += 1
        return payer_id

    def write_review_file(self, path: Path = DEFAULT_REVIEW_PATH) -> Path:
        """Write unmatched payer names, most frequent first, for a person to map or reject."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["hospital_id", "payer_name", "plan_name", "price_lines"])
            for (hospital_id, payer, plan), count in self.unmatched.most_common():
                writer.writerow([hospital_id, payer, plan, count])
        return path
